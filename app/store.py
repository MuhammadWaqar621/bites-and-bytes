"""In-memory conversation state: the cart, the coupon, the orders, the history.

Tool calling only becomes interesting once tools share state. `add_to_cart`
mutates the same cart that `calc_total` reads three tool calls later, and the
model never sees the cart object -- it only ever sees the JSON each tool
returns. This module owns that state.

State lives in a process dictionary keyed by session id, so restarting the
server clears everything. That is deliberate: swapping this file for Redis or
Postgres is the only change needed to make the app stateful, and nothing else
in the project would have to move.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

#: Order lifecycle, in the order it progresses. Index matters: `cancel_order`
#: refuses anything at or past "out_for_delivery".
ORDER_STAGES = ["confirmed", "preparing", "out_for_delivery", "delivered"]

#: Minutes after placement at which each stage begins. The status is therefore
#: derived from the clock rather than stored, which is what makes repeated
#: `order_status` calls return something that actually changes.
STAGE_AFTER_MINUTES = {"confirmed": 0, "preparing": 2, "out_for_delivery": 12,
                       "delivered": 30}


@dataclass
class CartLine:
    """One dish in the cart, with the quantity the customer asked for."""

    dish_id: str
    name: str
    unit_price: float
    quantity: int

    @property
    def line_total(self) -> float:
        return round(self.unit_price * self.quantity, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dish_id": self.dish_id,
            "name": self.name,
            "unit_price": self.unit_price,
            "quantity": self.quantity,
            "line_total": self.line_total,
        }


@dataclass
class Order:
    """A placed order. Its status is computed, never written."""

    order_id: str
    placed_at: datetime
    items: list[dict[str, Any]]
    totals: dict[str, float]
    customer: dict[str, str]
    eta_minutes: int
    cancelled: bool = False
    cancel_reason: str | None = None

    def status(self, now: datetime | None = None) -> str:
        """Derive the current stage from how long ago the order was placed."""
        if self.cancelled:
            return "cancelled"
        elapsed = ((now or datetime.now(timezone.utc)) - self.placed_at).total_seconds() / 60
        current = ORDER_STAGES[0]
        for stage in ORDER_STAGES:
            if elapsed >= STAGE_AFTER_MINUTES[stage]:
                current = stage
        return current

    def is_cancellable(self, now: datetime | None = None) -> bool:
        """Orders can only be pulled back before the rider leaves the kitchen."""
        return self.status(now) in ("confirmed", "preparing")

    def expected_at(self) -> datetime:
        return self.placed_at + timedelta(minutes=self.eta_minutes)


@dataclass
class Session:
    """Everything one browser tab accumulates while chatting."""

    session_id: str
    cart: dict[str, CartLine] = field(default_factory=dict)
    coupon_code: str | None = None
    orders: dict[str, Order] = field(default_factory=dict)
    #: Raw chat-completions messages (user / assistant / tool), minus the system
    #: prompt, which the agent prepends fresh on every turn.
    messages: list[dict[str, Any]] = field(default_factory=list)

    def subtotal(self) -> float:
        return round(sum(line.line_total for line in self.cart.values()), 2)

    def cart_as_list(self) -> list[dict[str, Any]]:
        return [line.as_dict() for line in self.cart.values()]

    def clear_cart(self) -> None:
        self.cart.clear()
        self.coupon_code = None


class SessionStore:
    """Thread-safe registry of sessions plus the global order-id counter.

    Uvicorn runs request handlers in a thread pool, so two requests really can
    touch this at the same time; the lock keeps the id counter honest.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._order_seq = itertools.count(1042)  # nicer-looking first order id

    def get(self, session_id: str) -> Session:
        """Return the session for this id, creating it on first contact."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = Session(session_id=session_id)
                self._sessions[session_id] = session
            return session

    def reset(self, session_id: str) -> Session:
        """Throw away a session and hand back a blank one under the same id."""
        with self._lock:
            session = Session(session_id=session_id)
            self._sessions[session_id] = session
            return session

    def next_order_id(self) -> str:
        with self._lock:
            return f"ORD-{next(self._order_seq)}"
