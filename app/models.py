"""ORM models -- the whole schema in one file.

Nine tables in three groups:

* **Catalogue** (read-mostly): ``dishes``, ``coupons``, ``delivery_zones``
* **Identity**: ``users``, ``auth_tokens``
* **Conversation**: ``conversations``, ``chat_messages``, ``tool_invocations``,
  ``cart_items``
* **Commerce**: ``orders``, ``order_items``

Two conventions worth knowing before you read on:

* **Money is ``Numeric(10, 2)``**, never float. Floats lose paise, and a
  restaurant bill that is off by a hundredth looks broken. The repository layer
  converts to ``float`` on the way out so JSON stays clean.
* **Datetimes are naive UTC.** Storing tz-aware values means DATETIMEOFFSET on
  SQL Server and something else everywhere else. Storing UTC and attaching the
  timezone on read is portable and never ambiguous.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    """Naive UTC 'now' -- the only timestamp source the models use."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

class Dish(Base):
    __tablename__ = "dishes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    diet: Mapped[str] = mapped_column(String(10), index=True)  # veg/nonveg/vegan
    price: Mapped[float] = mapped_column(Numeric(10, 2), index=True)
    spice: Mapped[str] = mapped_column(String(10))             # mild/medium/hot
    prep_minutes: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(400))
    #: Space-separated keywords. A junction table would be tidier, but this is
    #: read-only text the search LIKEs against -- not worth the extra join.
    tags: Mapped[str] = mapped_column(String(200), default="")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: Denormalised popularity counter, refreshed by the seeder. Lets
    #: `popular_dishes` answer without aggregating 34k order_items every time.
    times_ordered: Mapped[int] = mapped_column(Integer, default=0, index=True)

    # Covers the hot path in search_dish: "vegetarian, under 300".
    __table_args__ = (Index("ix_dishes_diet_price", "diet", "price"),)


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # percent/flat/free_delivery
    value: Mapped[float] = mapped_column(Numeric(10, 2))
    min_order: Mapped[float] = mapped_column(Numeric(10, 2))
    max_discount: Mapped[float] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    label: Mapped[str] = mapped_column(String(120))


class DeliveryZone(Base):
    __tablename__ = "delivery_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    pincode_prefix: Mapped[str] = mapped_column(String(3), unique=True, index=True)
    zone_name: Mapped[str] = mapped_column(String(80))
    fee: Mapped[float] = mapped_column(Numeric(10, 2))
    eta_minutes: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    #: PBKDF2-SHA256, salted, stored as "iterations$salt$hash". Never a plaintext
    #: password, and never reversible -- see app/security.py.
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")
    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class AuthToken(Base):
    """A logged-in browser session.

    The cookie holds a random token; this table holds only its SHA-256 hash, so
    a dump of this table cannot be replayed as a login.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    user: Mapped[User] = relationship()


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

class Conversation(Base):
    """One chat thread. A user may have many; each keeps its own cart."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="New chat")
    coupon_code: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
        order_by="ChatMessage.seq")
    cart_items: Mapped[list["CartItem"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan")


class ChatMessage(Base):
    """One entry of the raw chat-completions transcript.

    Every role is stored, including ``assistant`` messages that carry only
    ``tool_calls`` and the ``tool`` results that answer them. That is what makes
    the conversation resumable: the agent replays these rows verbatim, so the
    model sees exactly the history it saw before the server restarted.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True)
    #: Position within the conversation. Ordering by id would work today but
    #: breaks the moment rows are archived and re-inserted.
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # user/assistant/tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: JSON blob of the assistant's `tool_calls` array, when present.
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Set on `tool` rows: which request this result answers.
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_message_seq"),
    )


class ToolInvocation(Base):
    """Audit row for one executed tool call, powering the trace panel."""

    __tablename__ = "tool_invocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64), index=True)
    arguments_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean)
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CartItem(Base):
    """A dish in a conversation's cart. Persisted, so a refresh keeps it."""

    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id"))
    quantity: Mapped[int] = mapped_column(Integer)

    conversation: Mapped[Conversation] = relationship(back_populates="cart_items")
    dish: Mapped[Dish] = relationship()

    __table_args__ = (
        UniqueConstraint("conversation_id", "dish_id", name="uq_cart_dish"),
    )


# ---------------------------------------------------------------------------
# Commerce
# ---------------------------------------------------------------------------

class Order(Base):
    """A placed order.

    There is no ``status`` column on purpose: status is derived from
    ``placed_at`` and ``cancelled_at`` at read time, so a seeded order from last
    month reports ``delivered`` and one placed a minute ago reports
    ``confirmed`` without a background job ever touching a row.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    placed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    eta_minutes: Mapped[int] = mapped_column(Integer)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    customer_name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    address: Mapped[str] = mapped_column(String(250))
    pincode: Mapped[str] = mapped_column(String(10), index=True)
    payment_method: Mapped[str] = mapped_column(String(24))

    subtotal: Mapped[float] = mapped_column(Numeric(10, 2))
    discount: Mapped[float] = mapped_column(Numeric(10, 2))
    delivery_fee: Mapped[float] = mapped_column(Numeric(10, 2))
    packaging_fee: Mapped[float] = mapped_column(Numeric(10, 2))
    tax: Mapped[float] = mapped_column(Numeric(10, 2))
    total: Mapped[float] = mapped_column(Numeric(10, 2), index=True)
    coupon_code: Mapped[str | None] = mapped_column(String(24), nullable=True)

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan")

    # "this user's recent orders" -- the query find_past_orders runs.
    __table_args__ = (Index("ix_orders_user_placed", "user_id", "placed_at"),)


class OrderItem(Base):
    """A line on an order.

    Name and price are copied, not joined. An order is a historical record: if
    Butter Chicken goes up to 520 next month, a receipt from today must still
    say 480.
    """

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id"), index=True)
    dish_name: Mapped[str] = mapped_column(String(120))
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")
