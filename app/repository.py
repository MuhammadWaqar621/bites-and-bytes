"""Every SQL query in the project, in one layer.

`tools.py` calls these functions; it never builds a query itself. That
separation is what lets the same tool code run against SQL Server in
production and SQLite in the tests, and it keeps the interesting file --
`agent.py` -- free of persistence noise.

Boolean columns are compared with ``== True`` rather than ``.is_(True)``.
SQL Server has no BOOLEAN type, so ``.is_(True)`` renders as "IS 1" and is a
syntax error there; ``== True`` renders as "= 1" and works on every backend.

Money comes out of SQLAlchemy as `Decimal` (because the columns are
`Numeric`). Every function here converts to `float` on the way out, so callers
and JSON never have to think about it.
"""

from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session as DbSession

from app.data import ORDER_STAGES, STAGE_AFTER_MINUTES
from app.models import (
    AuthToken,
    CartItem,
    ChatMessage,
    Conversation,
    Coupon,
    DeliveryZone,
    Dish,
    Order,
    OrderItem,
    ToolInvocation,
    User,
    utcnow,
)
from app.security import hash_password, hash_token, new_session_token, verify_password


def money(value: Decimal | float | None) -> float:
    """Numeric column -> a JSON-safe float rounded to paise."""
    return round(float(value or 0), 2)


def _aware(value: datetime | None) -> datetime | None:
    """Naive UTC from the database -> tz-aware UTC for arithmetic."""
    return value.replace(tzinfo=timezone.utc) if value else None


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def dish_public(dish: Dish) -> dict[str, Any]:
    """The dish fields worth spending model tokens on."""
    return {
        "code": dish.code,
        "name": dish.name,
        "category": dish.category,
        "diet": dish.diet,
        "price": money(dish.price),
        "spice": dish.spice,
        "description": dish.description,
    }


def list_dishes(db: DbSession, category: str | None = None,
                limit: int = 60) -> list[Dish]:
    stmt = select(Dish).where(Dish.is_available == True)  # noqa: E712
    if category:
        stmt = stmt.where(func.lower(Dish.category) == category.strip().lower())
    stmt = stmt.order_by(Dish.category, Dish.times_ordered.desc(), Dish.name).limit(limit)
    return list(db.scalars(stmt))


def count_dishes(db: DbSession, category: str | None = None) -> int:
    stmt = select(func.count(Dish.id)).where(Dish.is_available == True)  # noqa: E712
    if category:
        stmt = stmt.where(func.lower(Dish.category) == category.strip().lower())
    return int(db.scalar(stmt) or 0)


def all_categories(db: DbSession) -> list[str]:
    return list(db.scalars(select(Dish.category).distinct().order_by(Dish.category)))


def search_dishes(
    db: DbSession,
    query: str | None = None,
    diet: str | None = None,
    max_price: float | None = None,
    spice: str | None = None,
    limit: int = 25,
) -> tuple[list[Dish], int]:
    """Filtered search. Returns ``(page, total_matches)``.

    With ~240 dishes the filtering could be done in Python, but doing it in SQL
    is the point: it stays fast at 240,000 and the `ix_dishes_diet_price` index
    covers the common "vegetarian under 300" shape.
    """
    stmt = select(Dish).where(Dish.is_available == True)  # noqa: E712

    if query:
        needle = f"%{query.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(Dish.name).like(needle),
            func.lower(Dish.description).like(needle),
            func.lower(Dish.tags).like(needle),
        ))
    if diet:
        wanted = diet.strip().lower()
        # Anything vegan is also acceptable to a vegetarian, so widen "veg".
        allowed = ["veg", "vegan"] if wanted == "veg" else [wanted]
        stmt = stmt.where(Dish.diet.in_(allowed))
    if max_price is not None:
        stmt = stmt.where(Dish.price <= Decimal(str(max_price)))
    if spice:
        stmt = stmt.where(Dish.spice == spice.strip().lower())

    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    page = list(db.scalars(
        stmt.order_by(Dish.times_ordered.desc(), Dish.price).limit(limit)))
    return page, total


def popular_dishes(db: DbSession, limit: int = 10,
                   diet: str | None = None) -> list[Dish]:
    """Best sellers, straight off the denormalised counter."""
    stmt = select(Dish).where(Dish.is_available == True,  # noqa: E712
                              Dish.times_ordered > 0)
    if diet:
        wanted = diet.strip().lower()
        allowed = ["veg", "vegan"] if wanted == "veg" else [wanted]
        stmt = stmt.where(Dish.diet.in_(allowed))
    return list(db.scalars(stmt.order_by(Dish.times_ordered.desc()).limit(limit)))


def resolve_dish(db: DbSession, name: str) -> tuple[Dish | None, list[str]]:
    """Find one dish from free text.

    Three passes, cheapest first: exact name or code, then a LIKE, then a fuzzy
    match for typos. Returns ``(dish, suggestions)``; when `dish` is None the
    suggestions become the hint the model reads back to the customer.
    """
    needle = (name or "").strip().lower()
    if not needle:
        return None, []

    exact = db.scalar(select(Dish).where(or_(
        func.lower(Dish.name) == needle,
        func.lower(Dish.code) == needle,
    )))
    if exact:
        return exact, []

    partial = list(db.scalars(
        select(Dish).where(func.lower(Dish.name).like(f"%{needle}%")).limit(6)))
    if len(partial) == 1:
        return partial[0], []
    if len(partial) > 1:
        return None, [d.name for d in partial]

    # Typo fallback. Only the names come back, so this stays cheap.
    names = list(db.scalars(select(Dish.name)))
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
    if len(close) == 1:
        return db.scalar(select(Dish).where(Dish.name == close[0])), []
    return None, close


def get_coupon(db: DbSession, code: str) -> Coupon | None:
    return db.scalar(select(Coupon).where(func.upper(Coupon.code) == code.strip().upper()))


def active_coupon_codes(db: DbSession, limit: int = 8) -> list[str]:
    return list(db.scalars(
        select(Coupon.code).where(Coupon.is_active == True)  # noqa: E712
        .order_by(Coupon.min_order).limit(limit)))


def list_coupons(db: DbSession) -> list[Coupon]:
    return list(db.scalars(select(Coupon).order_by(Coupon.min_order)))


def get_zone(db: DbSession, pincode: str) -> DeliveryZone | None:
    """Look up the delivery zone by pincode prefix, or None if unserviceable."""
    digits = re.sub(r"\D", "", pincode or "")
    if len(digits) != 6:
        return None
    return db.scalar(
        select(DeliveryZone).where(DeliveryZone.pincode_prefix == digits[:3]))


def serviceable_prefixes(db: DbSession, limit: int = 6) -> list[str]:
    return list(db.scalars(
        select(DeliveryZone.pincode_prefix).order_by(DeliveryZone.fee).limit(limit)))


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

def cart_lines(db: DbSession, conversation_id: int) -> list[tuple[CartItem, Dish]]:
    stmt = (select(CartItem, Dish).join(Dish, CartItem.dish_id == Dish.id)
            .where(CartItem.conversation_id == conversation_id)
            .order_by(CartItem.id))
    return [(item, dish) for item, dish in db.execute(stmt)]


def cart_view(db: DbSession, conversation_id: int) -> list[dict[str, Any]]:
    return [
        {
            "dish_code": dish.code,
            "name": dish.name,
            "unit_price": money(dish.price),
            "quantity": item.quantity,
            "line_total": money(Decimal(item.quantity) * dish.price),
        }
        for item, dish in cart_lines(db, conversation_id)
    ]


def cart_subtotal(db: DbSession, conversation_id: int) -> float:
    total = db.scalar(
        select(func.coalesce(func.sum(Dish.price * CartItem.quantity), 0))
        .select_from(CartItem).join(Dish, CartItem.dish_id == Dish.id)
        .where(CartItem.conversation_id == conversation_id))
    return money(total)


def add_cart_item(db: DbSession, conversation_id: int, dish: Dish,
                  quantity: int, cap: int) -> int:
    """Add to (or top up) a cart line. Returns the new line quantity."""
    item = db.scalar(select(CartItem).where(
        CartItem.conversation_id == conversation_id, CartItem.dish_id == dish.id))
    if item is None:
        item = CartItem(conversation_id=conversation_id, dish_id=dish.id, quantity=0)
        db.add(item)
    item.quantity = min(item.quantity + quantity, cap)
    db.flush()
    return item.quantity


def remove_cart_item(db: DbSession, conversation_id: int, dish: Dish,
                     quantity: int | None) -> int | None:
    """Remove some or all of a line. Returns how many were removed, or None."""
    item = db.scalar(select(CartItem).where(
        CartItem.conversation_id == conversation_id, CartItem.dish_id == dish.id))
    if item is None:
        return None

    removed = item.quantity if quantity is None else max(int(quantity), 1)
    if removed >= item.quantity:
        removed = item.quantity
        db.delete(item)
    else:
        item.quantity -= removed
    db.flush()
    return removed


def clear_cart(db: DbSession, conversation_id: int) -> None:
    db.execute(delete(CartItem).where(CartItem.conversation_id == conversation_id))


def cart_prep_minutes(db: DbSession, conversation_id: int) -> int:
    """Kitchen time for a cart = its slowest dish; the kitchen works in parallel."""
    slowest = db.scalar(
        select(func.max(Dish.prep_minutes)).select_from(CartItem)
        .join(Dish, CartItem.dish_id == Dish.id)
        .where(CartItem.conversation_id == conversation_id))
    return int(slowest or 0)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def order_status(order: Order, now: datetime | None = None) -> str:
    """Derive the stage from the clock. No status column, no cron job."""
    if order.cancelled_at is not None:
        return "cancelled"
    elapsed = ((now or datetime.now(timezone.utc)) - _aware(order.placed_at)).total_seconds() / 60
    current = ORDER_STAGES[0]
    for stage in ORDER_STAGES:
        if elapsed >= STAGE_AFTER_MINUTES[stage]:
            current = stage
    return current


def order_view(order: Order, include_items: bool = True) -> dict[str, Any]:
    """Serialise an order for the model and the UI.

    Times are emitted in local time: `get_current_time` reports the local
    clock, and a model shown two timezones will cheerfully tell the customer
    their food arrived before they ordered it.
    """
    placed = _aware(order.placed_at)
    view = {
        "order_id": order.code,
        "status": order_status(order),
        "placed_at": placed.astimezone().isoformat(timespec="seconds"),
        "expected_at": (placed + timedelta(minutes=order.eta_minutes))
                       .astimezone().isoformat(timespec="seconds"),
        "eta_minutes": order.eta_minutes,
        "customer": {
            "name": order.customer_name, "phone": order.phone,
            "address": order.address, "pincode": order.pincode,
            "payment_method": order.payment_method,
        },
        "totals": {
            "subtotal": money(order.subtotal), "discount": money(order.discount),
            "delivery_fee": money(order.delivery_fee),
            "packaging_fee": money(order.packaging_fee),
            "tax": money(order.tax), "total": money(order.total),
            "coupon_code": order.coupon_code,
        },
        "cancel_reason": order.cancel_reason,
    }
    if include_items:
        view["items"] = [
            {"name": item.dish_name, "quantity": item.quantity,
             "unit_price": money(item.unit_price),
             "line_total": money(Decimal(item.quantity) * item.unit_price)}
            for item in order.items
        ]
    return view


#: Order codes are the row id plus this offset, so the very first order reads
#: ORD-1042 rather than ORD-1. Both the seeder and the app derive codes through
#: `order_code_for`, which is what keeps them from ever colliding.
ORDER_CODE_OFFSET = 1041


def order_code_for(order_id: int) -> str:
    """The human-facing code for a given order row id."""
    return f"ORD-{ORDER_CODE_OFFSET + order_id}"


def next_order_code(db: DbSession) -> str:
    """Code for the order about to be inserted.

    MAX(id)+1 rather than a sequence keeps the codes readable and portable
    across SQL Server, Postgres and SQLite. Two orders placed in the same
    instant could collide; the unique index on `orders.code` catches that, and
    a real deployment would use a database sequence here.
    """
    highest = db.scalar(select(func.max(Order.id))) or 0
    return order_code_for(highest + 1)


def create_order(db: DbSession, user: User, conversation: Conversation,
                 customer: dict[str, str], totals: dict[str, float],
                 eta_minutes: int) -> Order:
    """Persist the cart as an order, then empty the cart."""
    order = Order(
        code=next_order_code(db),
        user_id=user.id,
        placed_at=utcnow(),
        eta_minutes=eta_minutes,
        customer_name=customer["name"], phone=customer["phone"],
        address=customer["address"], pincode=customer["pincode"],
        payment_method=customer["payment_method"],
        subtotal=Decimal(str(totals["subtotal"])),
        discount=Decimal(str(totals["discount"])),
        delivery_fee=Decimal(str(totals["delivery_fee"])),
        packaging_fee=Decimal(str(totals["packaging_fee"])),
        tax=Decimal(str(totals["tax"])),
        total=Decimal(str(totals["total"])),
        coupon_code=conversation.coupon_code,
    )
    db.add(order)
    db.flush()

    for item, dish in cart_lines(db, conversation.id):
        db.add(OrderItem(order_id=order.id, dish_id=dish.id, dish_name=dish.name,
                         unit_price=dish.price, quantity=item.quantity))
        dish.times_ordered += item.quantity

    clear_cart(db, conversation.id)
    conversation.coupon_code = None
    db.flush()
    return order


def get_order(db: DbSession, user: User, code: str) -> Order | None:
    """Fetch one order **belonging to this user**.

    The `user_id` predicate is the authorisation check: without it, a model
    that hallucinated somebody else's order id would happily read a stranger's
    address and phone number back to the customer.
    """
    return db.scalar(select(Order).where(
        Order.user_id == user.id,
        func.upper(Order.code) == code.strip().upper()))


def list_orders(db: DbSession, user: User, limit: int = 5) -> list[Order]:
    return list(db.scalars(
        select(Order).where(Order.user_id == user.id)
        .order_by(Order.placed_at.desc()).limit(limit)))


def recent_order_codes(db: DbSession, user: User, limit: int = 5) -> list[str]:
    return list(db.scalars(
        select(Order.code).where(Order.user_id == user.id)
        .order_by(Order.placed_at.desc()).limit(limit)))


def latest_order(db: DbSession, user: User) -> Order | None:
    return db.scalar(select(Order).where(Order.user_id == user.id)
                     .order_by(Order.placed_at.desc()).limit(1))


def favourite_dishes(db: DbSession, user: User, limit: int = 5) -> list[tuple[str, int]]:
    """This customer's most-ordered dishes -- the 'usual'."""
    stmt = (select(OrderItem.dish_name, func.sum(OrderItem.quantity).label("n"))
            .join(Order, OrderItem.order_id == Order.id)
            .where(Order.user_id == user.id, Order.cancelled_at.is_(None))
            .group_by(OrderItem.dish_name)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(limit))
    return [(name, int(n)) for name, n in db.execute(stmt)]


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def spend_stats(db: DbSession, user: User, since: datetime | None = None,
                until: datetime | None = None) -> dict[str, Any]:
    """Order count, total spend and average order value over a window.

    Cancelled orders are excluded from the money but counted separately -- a
    customer asking "what did I spend this month" does not mean "including the
    order I cancelled".
    """
    live = [Order.user_id == user.id, Order.cancelled_at.is_(None)]
    every = [Order.user_id == user.id]
    if since is not None:
        live.append(Order.placed_at >= since)
        every.append(Order.placed_at >= since)
    if until is not None:
        live.append(Order.placed_at < until)
        every.append(Order.placed_at < until)

    count, total = db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total), 0))
        .where(*live)).one()
    cancelled = db.scalar(
        select(func.count(Order.id)).where(*every, Order.cancelled_at.is_not(None)))

    count = int(count)
    return {
        "order_count": count,
        "total_spend": money(total),
        "average_order_value": money(float(total) / count) if count else 0.0,
        "cancelled_count": int(cancelled or 0),
    }


def orders_in_window(db: DbSession, user: User, since: datetime,
                     until: datetime) -> list[tuple[datetime, float]]:
    """``(placed_at, total)`` for every live order in the window.

    Bucketing into days/weeks/months happens in Python rather than SQL because
    the date functions differ per engine (`FORMAT` on SQL Server, `strftime` on
    SQLite, `date_trunc` on Postgres) and this project is meant to run on all
    three unchanged. The row count is bounded -- it is one customer's orders
    over a bounded window, and `ix_orders_user_placed` covers the lookup.
    """
    rows = db.execute(
        select(Order.placed_at, Order.total)
        .where(Order.user_id == user.id, Order.cancelled_at.is_(None),
               Order.placed_at >= since, Order.placed_at < until)
        .order_by(Order.placed_at)).all()
    return [(_aware(placed_at), money(total)) for placed_at, total in rows]


def spend_by_category(db: DbSession, user: User, since: datetime | None,
                      limit: int) -> list[tuple[str, float, int]]:
    """Spend grouped by menu section -- a plain SQL GROUP BY, portable as-is."""
    where = [Order.user_id == user.id, Order.cancelled_at.is_(None)]
    if since is not None:
        where.append(Order.placed_at >= since)

    stmt = (select(Dish.category,
                   func.sum(OrderItem.unit_price * OrderItem.quantity),
                   func.sum(OrderItem.quantity))
            .select_from(OrderItem)
            .join(Order, OrderItem.order_id == Order.id)
            .join(Dish, OrderItem.dish_id == Dish.id)
            .where(*where)
            .group_by(Dish.category)
            .order_by(func.sum(OrderItem.unit_price * OrderItem.quantity).desc())
            .limit(limit))
    return [(name, money(spend), int(units)) for name, spend, units in db.execute(stmt)]


def spend_by_dish(db: DbSession, user: User, since: datetime | None,
                  limit: int) -> list[tuple[str, float, int]]:
    where = [Order.user_id == user.id, Order.cancelled_at.is_(None)]
    if since is not None:
        where.append(Order.placed_at >= since)

    stmt = (select(OrderItem.dish_name,
                   func.sum(OrderItem.unit_price * OrderItem.quantity),
                   func.sum(OrderItem.quantity))
            .join(Order, OrderItem.order_id == Order.id)
            .where(*where)
            .group_by(OrderItem.dish_name)
            .order_by(func.sum(OrderItem.unit_price * OrderItem.quantity).desc())
            .limit(limit))
    return [(name, money(spend), int(units)) for name, spend, units in db.execute(stmt)]


def spend_by_payment_method(db: DbSession, user: User, since: datetime | None,
                            limit: int) -> list[tuple[str, float, int]]:
    where = [Order.user_id == user.id, Order.cancelled_at.is_(None)]
    if since is not None:
        where.append(Order.placed_at >= since)

    stmt = (select(Order.payment_method, func.sum(Order.total), func.count(Order.id))
            .where(*where)
            .group_by(Order.payment_method)
            .order_by(func.sum(Order.total).desc())
            .limit(limit))
    return [(name, money(spend), int(n)) for name, spend, n in db.execute(stmt)]


def order_stats(db: DbSession, user: User) -> dict[str, Any]:
    row = db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total), 0))
        .where(Order.user_id == user.id, Order.cancelled_at.is_(None))).one()
    return {"order_count": int(row[0]), "lifetime_spend": money(row[1])}


# ---------------------------------------------------------------------------
# Users and auth
# ---------------------------------------------------------------------------

def get_user_by_email(db: DbSession, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def create_user(db: DbSession, email: str, password: str,
                display_name: str | None = None, phone: str | None = None,
                address: str | None = None, pincode: str | None = None) -> User:
    """Register an account. Only the email and password are required.

    A blank display name falls back to the local part of the email, so the
    assistant always has something to call the customer.
    """
    email = email.strip().lower()
    user = User(
        email=email,
        display_name=(display_name or "").strip() or email.split("@")[0],
        password_hash=hash_password(password),
        default_phone=_clean(phone),
        default_address=_clean(address),
        default_pincode=_clean(pincode),
    )
    db.add(user)
    db.flush()
    return user


def _clean(value: str | None) -> str | None:
    """Trim an optional field, turning blank input into a real NULL."""
    trimmed = (value or "").strip()
    return trimmed or None


def authenticate(db: DbSession, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if user and verify_password(password, user.password_hash):
        return user
    return None


def issue_token(db: DbSession, user: User, days: int) -> str:
    """Create a login session and return the raw token for the cookie."""
    token, digest = new_session_token()
    db.add(AuthToken(user_id=user.id, token_hash=digest,
                     expires_at=utcnow() + timedelta(days=days)))
    user.last_seen_at = utcnow()
    db.flush()
    return token


def user_for_token(db: DbSession, token: str) -> User | None:
    """Resolve a cookie token to a user, ignoring expired sessions."""
    if not token:
        return None
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    if row is None or row.expires_at < utcnow():
        return None
    return row.user


def revoke_token(db: DbSession, token: str) -> None:
    db.execute(delete(AuthToken).where(AuthToken.token_hash == hash_token(token)))


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def create_conversation(db: DbSession, user: User, title: str = "New chat") -> Conversation:
    conversation = Conversation(user_id=user.id, title=title[:120])
    db.add(conversation)
    db.flush()
    return conversation


def get_conversation(db: DbSession, user: User, conversation_id: int) -> Conversation | None:
    """Fetch a chat **belonging to this user** -- the ownership check."""
    return db.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user.id))


def list_conversations(db: DbSession, user: User, limit: int = 50) -> list[dict[str, Any]]:
    """Sidebar listing: title, last activity and message count per chat."""
    counts = (select(ChatMessage.conversation_id,
                     func.count(ChatMessage.id).label("n"))
              .where(ChatMessage.role == "user")
              .group_by(ChatMessage.conversation_id).subquery())

    stmt = (select(Conversation, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.conversation_id == Conversation.id)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc()).limit(limit))

    return [
        {
            "id": conversation.id,
            "title": conversation.title,
            "message_count": int(count),
            "updated_at": _aware(conversation.updated_at).astimezone()
                          .isoformat(timespec="seconds"),
        }
        for conversation, count in db.execute(stmt)
    ]


def delete_conversation(db: DbSession, user: User, conversation_id: int) -> bool:
    conversation = get_conversation(db, user, conversation_id)
    if conversation is None:
        return False
    db.execute(delete(ToolInvocation).where(
        ToolInvocation.conversation_id == conversation_id))
    db.delete(conversation)  # cascades to messages and cart items
    return True


def next_seq(db: DbSession, conversation_id: int) -> int:
    highest = db.scalar(select(func.max(ChatMessage.seq))
                        .where(ChatMessage.conversation_id == conversation_id))
    return int(highest or 0) + 1


def append_message(db: DbSession, conversation: Conversation, role: str,
                   content: str | None = None, tool_calls: list[dict] | None = None,
                   tool_call_id: str | None = None,
                   tool_name: str | None = None) -> ChatMessage:
    """Persist one transcript row and bump the conversation's activity time."""
    message = ChatMessage(
        conversation_id=conversation.id,
        seq=next_seq(db, conversation.id),
        role=role,
        content=content,
        tool_calls_json=json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
    db.add(message)
    conversation.updated_at = utcnow()
    db.flush()
    return message


def load_messages(db: DbSession, conversation_id: int,
                  limit: int | None = None) -> list[ChatMessage]:
    """Transcript rows in order. `limit` takes the most recent N."""
    stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    if limit:
        newest = list(db.scalars(stmt.order_by(ChatMessage.seq.desc()).limit(limit)))
        return list(reversed(newest))
    return list(db.scalars(stmt.order_by(ChatMessage.seq)))


def message_as_api_dict(message: ChatMessage) -> dict[str, Any]:
    """Rebuild the exact chat-completions payload this row came from."""
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id,
                "name": message.tool_name, "content": message.content}
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls_json:
        payload["tool_calls"] = json.loads(message.tool_calls_json)
    return payload


def visible_transcript(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Just the user/assistant turns, for repainting the chat window."""
    return [
        {"role": m.role, "content": m.content,
         "at": _aware(m.created_at).astimezone().isoformat(timespec="seconds")}
        for m in messages
        if m.role in ("user", "assistant") and m.content
    ]


def record_invocation(db: DbSession, conversation_id: int, turn: int, name: str,
                      arguments: dict, result: dict, ok: bool, duration_ms: int) -> None:
    db.add(ToolInvocation(
        conversation_id=conversation_id, turn=turn, name=name,
        arguments_json=json.dumps(arguments, ensure_ascii=False),
        result_json=json.dumps(result, ensure_ascii=False),
        ok=ok, duration_ms=duration_ms))


def list_invocations(db: DbSession, conversation_id: int,
                     limit: int = 60) -> list[dict[str, Any]]:
    """Tool trace for a conversation, newest turn first."""
    rows = list(db.scalars(
        select(ToolInvocation)
        .where(ToolInvocation.conversation_id == conversation_id)
        .order_by(ToolInvocation.id.desc()).limit(limit)))
    return [
        {"turn": row.turn, "name": row.name, "ok": row.ok,
         "duration_ms": row.duration_ms,
         "arguments": json.loads(row.arguments_json),
         "result": json.loads(row.result_json)}
        for row in reversed(rows)
    ]


def next_turn(db: DbSession, conversation_id: int) -> int:
    """Turn number for the exchange now in progress.

    Defined as *how many user messages this conversation has had* -- called
    after the incoming one is stored, so the first exchange is turn 1. Deriving
    it from MAX(tool_invocations.turn) instead would skip a number whenever a
    turn used no tools, and then the UI could not line a stored chart up with
    the reply it belongs to when repainting the conversation.
    """
    return int(db.scalar(
        select(func.count(ChatMessage.id))
        .where(ChatMessage.conversation_id == conversation_id,
               ChatMessage.role == "user")) or 0)
