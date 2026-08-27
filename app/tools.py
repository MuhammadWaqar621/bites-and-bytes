"""The fifteen tools the model is allowed to call.

Design rules every function here follows -- they are the actual lesson:

1. **No SQL, no LLM.** Tools call `repository.py` for data and never build a
   query or touch the model. That is why the same tool code runs against SQL
   Server in production and SQLite in the tests.
2. **One envelope for every return value.** Success is ``{"ok": True, ...}``
   and failure is ``{"ok": False, "error": ..., "hint": ...}``. The model reads
   that ``hint`` and turns it into a helpful sentence, so a good hint is worth
   more than a good stack trace.
3. **Never raise.** An exception would abort the agent loop. A tool that
   cannot do its job returns ``ok: False`` and lets the model recover.
4. **Return facts, not prose.** Tools return numbers and ids; the model does
   the wording. That is why the assistant can never invent a price.
5. **Every user-scoped query is filtered by ``ctx.user``.** A model that
   hallucinates somebody else's order id must get "not found", never a
   stranger's address. Authorisation lives here, not in the prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy.orm import Session as DbSession

from app import repository as repo
from app.data import (
    CURRENCY,
    PACKAGING_FEE,
    PAYMENT_METHODS,
    SUPPORT_NUMBER,
    TAX_RATE,
)
from app.models import Conversation, User, utcnow

MAX_QUANTITY_PER_DISH = 20


@dataclass
class ToolContext:
    """Everything a tool is allowed to know: the database, who is asking, and
    which chat they are asking in."""

    db: DbSession
    user: User
    conversation: Conversation


ToolFn = Callable[..., dict[str, Any]]


# ---------------------------------------------------------------------------
# Internal helpers (not exposed to the model)
# ---------------------------------------------------------------------------

def _coupon_discount(ctx: ToolContext, code: str | None,
                     subtotal: float) -> tuple[float, bool, str | None]:
    """Return ``(discount, free_delivery, label)`` for the applied coupon."""
    if not code:
        return 0.0, False, None
    coupon = repo.get_coupon(ctx.db, code)
    # The coupon was valid when applied; the cart may have shrunk since.
    if not coupon or not coupon.is_active or subtotal < float(coupon.min_order):
        return 0.0, False, None

    if coupon.kind == "percent":
        raw = subtotal * float(coupon.value) / 100
        return repo.money(min(raw, float(coupon.max_discount))), False, coupon.label
    if coupon.kind == "flat":
        return repo.money(min(float(coupon.value), subtotal)), False, coupon.label
    return 0.0, True, coupon.label  # free_delivery


def _price_breakdown(ctx: ToolContext, pincode: str | None) -> dict[str, Any]:
    """The single source of truth for money. Used by calc_total and place_order."""
    subtotal = repo.cart_subtotal(ctx.db, ctx.conversation.id)
    discount, free_delivery, label = _coupon_discount(
        ctx, ctx.conversation.coupon_code, subtotal)

    zone = repo.get_zone(ctx.db, pincode) if pincode else None
    delivery_fee = 0.0 if (zone is None or free_delivery) else float(zone.fee)

    taxable = max(subtotal - discount, 0.0)
    tax = repo.money(taxable * TAX_RATE)
    total = repo.money(taxable + delivery_fee + PACKAGING_FEE + tax)

    return {
        "currency": CURRENCY,
        "subtotal": repo.money(subtotal),
        "coupon_code": ctx.conversation.coupon_code,
        "coupon_label": label,
        "discount": repo.money(discount),
        "delivery_fee": repo.money(delivery_fee),
        # Makes it explicit that a missing pincode means a missing fee, so the
        # model asks instead of quoting an incomplete total.
        "delivery_fee_included": zone is not None or free_delivery,
        "delivery_zone": zone.zone_name if zone else None,
        "packaging_fee": repo.money(PACKAGING_FEE),
        "tax": tax,
        "tax_rate_percent": round(TAX_RATE * 100, 2),
        "total": total,
    }


def _unserviceable(ctx: ToolContext, pincode: str) -> dict[str, Any]:
    prefixes = repo.serviceable_prefixes(ctx.db)
    return {
        "ok": False,
        "error": f"We do not deliver to pincode '{pincode}'.",
        "hint": "We serve 6-digit pincodes starting " + ", ".join(prefixes) + ".",
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def get_menu(ctx: ToolContext, category: str | None = None) -> dict[str, Any]:
    """Tool 1 -- read-only, one optional filter."""
    if category and category.strip().lower() not in {
        c.lower() for c in repo.all_categories(ctx.db)
    }:
        return {
            "ok": False,
            "error": f"No category named '{category}'.",
            "hint": "Valid categories: " + ", ".join(repo.all_categories(ctx.db)),
        }

    dishes = repo.list_dishes(ctx.db, category)
    total = repo.count_dishes(ctx.db, category)
    return {
        "ok": True,
        "currency": CURRENCY,
        "showing": len(dishes),
        "total_available": total,
        "dishes": [repo.dish_public(d) for d in dishes],
        # The menu is far too big to send whole, so say so rather than let the
        # model imply this list is everything.
        "note": None if len(dishes) == total else
                f"Showing the {len(dishes)} most popular of {total} dishes. "
                "Use search_dish for anything more specific.",
    }


def search_dish(
    ctx: ToolContext,
    query: str | None = None,
    diet: str | None = None,
    max_price: float | None = None,
    spice: str | None = None,
) -> dict[str, Any]:
    """Tool 2 -- several optional filters, combined with AND, resolved in SQL."""
    if diet and diet.strip().lower() not in {"veg", "nonveg", "vegan"}:
        return {"ok": False, "error": f"Unknown diet '{diet}'.",
                "hint": "diet must be one of: veg, nonveg, vegan."}

    dishes, total = repo.search_dishes(ctx.db, query, diet, max_price, spice)
    return {
        "ok": True,
        "currency": CURRENCY,
        "showing": len(dishes),
        "total_matches": total,
        "filters_applied": {"query": query, "diet": diet,
                            "max_price": max_price, "spice": spice},
        "dishes": [repo.dish_public(d) for d in dishes],
        # An empty result is a success, not an error -- but say so plainly, or
        # the model will fill the gap from memory.
        "note": None if dishes else "Nothing on the menu matches those filters.",
    }


def popular_dishes(ctx: ToolContext, limit: int = 8,
                   diet: str | None = None) -> dict[str, Any]:
    """Tool 3 -- best sellers, ranked across every order ever placed."""
    limit = max(1, min(int(limit or 8), 20))
    dishes = repo.popular_dishes(ctx.db, limit, diet)
    return {
        "ok": True,
        "currency": CURRENCY,
        "basis": "units sold across all customers",
        "dishes": [
            {**repo.dish_public(d), "times_ordered": d.times_ordered} for d in dishes
        ],
    }


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

def add_to_cart(ctx: ToolContext, dish_name: str, quantity: int = 1) -> dict[str, Any]:
    """Tool 4 -- a write, plus the fuzzy-matching failure path."""
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"quantity '{quantity}' is not a number.",
                "hint": "Pass quantity as an integer, e.g. 2."}
    if quantity < 1 or quantity > MAX_QUANTITY_PER_DISH:
        return {"ok": False,
                "error": f"quantity must be between 1 and {MAX_QUANTITY_PER_DISH}.",
                "hint": "For larger orders the customer should call the restaurant."}

    dish, suggestions = repo.resolve_dish(ctx.db, dish_name)
    if dish is None:
        return {"ok": False, "error": f"No dish matching '{dish_name}'.",
                "hint": ("Did you mean: " + ", ".join(suggestions) + "?") if suggestions
                else "Call search_dish and offer the customer real options."}

    line_quantity = repo.add_cart_item(
        ctx.db, ctx.conversation.id, dish, quantity, MAX_QUANTITY_PER_DISH)
    return {
        "ok": True,
        "added": {"name": dish.name, "quantity": quantity,
                  "unit_price": repo.money(dish.price)},
        "line_quantity": line_quantity,
        "cart": repo.cart_view(ctx.db, ctx.conversation.id),
        "cart_subtotal": repo.cart_subtotal(ctx.db, ctx.conversation.id),
        "currency": CURRENCY,
    }


def view_cart(ctx: ToolContext) -> dict[str, Any]:
    """Tool 5 -- the zero-argument case. Its schema still needs a properties block."""
    items = repo.cart_view(ctx.db, ctx.conversation.id)
    return {
        "ok": True,
        "currency": CURRENCY,
        "items": items,
        "item_count": sum(line["quantity"] for line in items),
        "subtotal": repo.cart_subtotal(ctx.db, ctx.conversation.id),
        "coupon_code": ctx.conversation.coupon_code,
        "note": None if items else "The cart is empty.",
    }


def remove_from_cart(ctx: ToolContext, dish_name: str,
                     quantity: int | None = None) -> dict[str, Any]:
    """Tool 6 -- destructive, so it validates before it deletes."""
    dish, suggestions = repo.resolve_dish(ctx.db, dish_name)
    if dish is None:
        return {"ok": False, "error": f"No dish matching '{dish_name}'.",
                "hint": ("Did you mean: " + ", ".join(suggestions) + "?") if suggestions
                else "Call view_cart to see what is actually in the cart."}

    removed = repo.remove_cart_item(ctx.db, ctx.conversation.id, dish, quantity)
    if removed is None:
        return {"ok": False, "error": f"{dish.name} is not in the cart.",
                "hint": "Call view_cart before removing items."}

    return {
        "ok": True,
        "removed": {"name": dish.name, "quantity": removed},
        "cart": repo.cart_view(ctx.db, ctx.conversation.id),
        "cart_subtotal": repo.cart_subtotal(ctx.db, ctx.conversation.id),
        "currency": CURRENCY,
    }


def apply_coupon(ctx: ToolContext, code: str) -> dict[str, Any]:
    """Tool 7 -- a tool whose *failures* carry the interesting information."""
    key = (code or "").strip().upper()
    coupon = repo.get_coupon(ctx.db, key)
    active = repo.active_coupon_codes(ctx.db)

    if coupon is None:
        return {"ok": False, "error": f"'{code}' is not a valid coupon code.",
                "hint": "Active codes include: " + ", ".join(active)}
    if not coupon.is_active:
        return {"ok": False, "error": f"Coupon {key} has expired.",
                "hint": "Offer an active code instead: " + ", ".join(active)}

    subtotal = repo.cart_subtotal(ctx.db, ctx.conversation.id)
    if subtotal < float(coupon.min_order):
        shortfall = repo.money(float(coupon.min_order) - subtotal)
        return {
            "ok": False,
            "error": f"{key} needs a subtotal of at least {repo.money(coupon.min_order)}.",
            "hint": f"The cart is {CURRENCY}{shortfall} short. Suggest one more dish.",
            "current_subtotal": subtotal,
        }

    ctx.conversation.coupon_code = key
    ctx.db.flush()
    discount, free_delivery, label = _coupon_discount(ctx, key, subtotal)
    return {
        "ok": True, "code": key, "label": label, "discount": discount,
        "free_delivery": free_delivery,
        "subtotal_after_discount": repo.money(subtotal - discount),
        "currency": CURRENCY,
    }


def calc_total(ctx: ToolContext, pincode: str | None = None) -> dict[str, Any]:
    """Tool 8 -- arithmetic the model must never attempt itself."""
    items = repo.cart_view(ctx.db, ctx.conversation.id)
    if not items:
        return {"ok": False, "error": "The cart is empty, so there is nothing to total.",
                "hint": "Add at least one dish with add_to_cart first."}

    breakdown = _price_breakdown(ctx, pincode)
    breakdown["ok"] = True
    breakdown["items"] = items
    if not breakdown["delivery_fee_included"]:
        breakdown["hint"] = (
            "No serviceable pincode was supplied, so delivery is not in this total. "
            "Ask the customer for their 6-digit pincode."
        )
    return breakdown


def estimate_delivery(ctx: ToolContext, pincode: str) -> dict[str, Any]:
    """Tool 9 -- a derived value with a genuine 'we do not serve you' path."""
    zone = repo.get_zone(ctx.db, pincode)
    if zone is None:
        return _unserviceable(ctx, pincode)

    prep = repo.cart_prep_minutes(ctx.db, ctx.conversation.id)
    return {
        "ok": True, "pincode": pincode, "zone": zone.zone_name,
        "delivery_fee": repo.money(zone.fee),
        "travel_minutes": zone.eta_minutes,
        "kitchen_minutes": prep,
        # Total ETA = slowest dish in the kitchen + time on the road.
        "total_eta_minutes": prep + zone.eta_minutes,
        "currency": CURRENCY,
    }


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def place_order(ctx: ToolContext, customer_name: str, phone: str, address: str,
                pincode: str, payment_method: str) -> dict[str, Any]:
    """Tool 10 -- five required arguments.

    This is the tool that teaches the model to *stop and ask*: it cannot be
    called until the customer has supplied all five, so the model has to gather
    them in conversation first.
    """
    if not repo.cart_view(ctx.db, ctx.conversation.id):
        return {"ok": False, "error": "Cannot place an empty order.",
                "hint": "Add dishes with add_to_cart first."}

    digits = re.sub(r"\D", "", phone or "")
    if len(digits) != 10:
        return {"ok": False, "error": f"'{phone}' is not a valid 10-digit phone number.",
                "hint": "Ask the customer to repeat their 10-digit mobile number."}

    method = (payment_method or "").strip().lower().replace(" ", "_")
    if method not in PAYMENT_METHODS:
        return {"ok": False,
                "error": f"'{payment_method}' is not a supported payment method.",
                "hint": "Supported methods: " + ", ".join(PAYMENT_METHODS)}

    zone = repo.get_zone(ctx.db, pincode)
    if zone is None:
        return _unserviceable(ctx, pincode)

    if not (customer_name or "").strip() or len((address or "").strip()) < 8:
        return {"ok": False, "error": "Name and a full street address are both required.",
                "hint": "Ask for the flat/house number and street, not just the area."}

    breakdown = _price_breakdown(ctx, pincode)
    eta = repo.cart_prep_minutes(ctx.db, ctx.conversation.id) + zone.eta_minutes

    order = repo.create_order(
        ctx.db, ctx.user, ctx.conversation,
        customer={"name": customer_name.strip(), "phone": digits,
                  "address": address.strip(), "pincode": pincode,
                  "payment_method": method},
        totals=breakdown, eta_minutes=eta,
    )
    return {"ok": True, "order": repo.order_view(order),
            "message": "Order placed. Quote this order_id to the customer verbatim."}


def order_status(ctx: ToolContext, order_id: str) -> dict[str, Any]:
    """Tool 11 -- lookup by an id, scoped to the signed-in user."""
    order = repo.get_order(ctx.db, ctx.user, order_id)
    if order is None:
        known = repo.recent_order_codes(ctx.db, ctx.user)
        return {"ok": False, "error": f"No order with id '{order_id}' on this account.",
                "hint": ("This customer's recent orders: " + ", ".join(known)) if known
                else "This customer has not placed any orders yet."}
    return {"ok": True, "order": repo.order_view(order)}


def cancel_order(ctx: ToolContext, order_id: str,
                 reason: str | None = None) -> dict[str, Any]:
    """Tool 12 -- a business rule the model must relay rather than override."""
    order = repo.get_order(ctx.db, ctx.user, order_id)
    if order is None:
        return {"ok": False, "error": f"No order with id '{order_id}' on this account.",
                "hint": "Confirm the order id with the customer."}

    if order.cancelled_at is not None:
        return {"ok": False, "error": f"{order.code} was already cancelled.",
                "hint": "Nothing further to do."}

    status = repo.order_status(order)
    if status not in ("confirmed", "preparing"):
        return {
            "ok": False,
            "error": f"{order.code} is '{status}' and can no longer be cancelled.",
            "hint": "Once the rider has collected the food, only the support desk "
                    f"on {SUPPORT_NUMBER} can help.",
        }

    order.cancelled_at = utcnow()
    order.cancel_reason = (reason or "").strip() or "No reason given"
    ctx.db.flush()
    return {"ok": True, "order_id": order.code, "status": "cancelled",
            "reason": order.cancel_reason,
            "refund_note": "Prepaid orders are refunded within 3-5 working days."}


def find_past_orders(ctx: ToolContext, limit: int = 5) -> dict[str, Any]:
    """Tool 13 -- history. Only exists because the orders are in a database.

    Scoped to ``ctx.user``, so one customer can never read another's history
    however the model phrases the request.
    """
    limit = max(1, min(int(limit or 5), 10))
    orders = repo.list_orders(ctx.db, ctx.user, limit)
    if not orders:
        return {"ok": True, "count": 0, "orders": [],
                "note": "This customer has not ordered before."}
    return {
        "ok": True,
        "count": len(orders),
        "currency": CURRENCY,
        "orders": [repo.order_view(order) for order in orders],
    }


def get_my_profile(ctx: ToolContext) -> dict[str, Any]:
    """Tool 14 -- the customer's own history, condensed.

    Call it once at the start of an order and the assistant can offer "same
    address as last time?" instead of asking for five fields again. This is
    what "the app remembers me" actually means in practice.
    """
    user = ctx.user
    stats = repo.order_stats(ctx.db, user)
    latest = repo.latest_order(ctx.db, user)
    favourites = repo.favourite_dishes(ctx.db, user)

    # Prefer the last real order; fall back to whatever was typed at sign-up so
    # a brand-new account still has something to offer. Either way the model
    # must confirm before ordering with these.
    if latest is not None:
        saved = {"name": latest.customer_name, "phone": latest.phone,
                 "address": latest.address, "pincode": latest.pincode,
                 "payment_method": latest.payment_method,
                 "source": "their last order"}
    elif user.default_phone or user.default_address:
        saved = {"name": user.display_name, "phone": user.default_phone,
                 "address": user.default_address, "pincode": user.default_pincode,
                 "payment_method": None, "source": "their sign-up details"}
    else:
        saved = None

    return {
        "ok": True,
        "display_name": user.display_name,
        "email": user.email,
        "currency": CURRENCY,
        **stats,
        "usual_dishes": [{"name": name, "times_ordered": n} for name, n in favourites],
        "saved_delivery": saved,
        "hint": "Offer these saved details for reuse, but confirm with the customer "
                "before placing an order with them. Any field that is null must "
                "still be asked for."
                if saved else
                "Nothing is saved for this customer yet -- ask for the delivery "
                "details normally.",
    }


def get_current_time(ctx: ToolContext) -> dict[str, Any]:
    """Tool 15 -- grounding. A language model has no clock of its own."""
    now = datetime.now().astimezone()
    return {
        "ok": True,
        "iso": now.isoformat(timespec="seconds"),
        "human": now.strftime("%A, %d %B %Y, %I:%M %p"),
        "hour_24": now.hour,
        # Lets the model answer "are you open?" without a separate tool.
        "kitchen_open": 11 <= now.hour < 23,
        "kitchen_hours": "11:00 to 23:00 daily",
    }


#: The dispatch table the agent loop uses. The keys MUST match the `name`
#: fields in :mod:`app.schemas` exactly -- that string is the only contract
#: between the model and this file.
TOOL_REGISTRY: dict[str, ToolFn] = {
    "get_menu": get_menu,
    "search_dish": search_dish,
    "popular_dishes": popular_dishes,
    "add_to_cart": add_to_cart,
    "view_cart": view_cart,
    "remove_from_cart": remove_from_cart,
    "apply_coupon": apply_coupon,
    "calc_total": calc_total,
    "estimate_delivery": estimate_delivery,
    "place_order": place_order,
    "order_status": order_status,
    "cancel_order": cancel_order,
    "find_past_orders": find_past_orders,
    "get_my_profile": get_my_profile,
    "get_current_time": get_current_time,
}
