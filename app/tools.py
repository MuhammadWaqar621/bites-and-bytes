"""The twelve tools the model is allowed to call.

Design rules every function here follows -- they are the actual lesson:

1. **Plain Python.** No function imports the LLM. Each one is callable and
   testable on its own (see ``tests/test_tools.py``), which means you can debug
   business logic without spending a single token.
2. **One envelope for every return value.** Success is
   ``{"ok": True, ...}`` and failure is
   ``{"ok": False, "error": ..., "hint": ...}``. The model reads that ``hint``
   and turns it into a helpful sentence, so a good hint is worth more than a
   good stack trace.
3. **Never raise.** An exception would abort the agent loop. A tool that
   cannot do its job returns ``ok: False`` and lets the model recover -- that is
   what makes the assistant feel resilient instead of brittle.
4. **Return facts, not prose.** Tools return numbers and ids; the model does
   the wording. That separation is why the assistant can never invent a price.
"""

from __future__ import annotations

import difflib
import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.data import (
    COUPONS,
    CURRENCY,
    DELIVERY_ZONES,
    MENU,
    PACKAGING_FEE,
    PAYMENT_METHODS,
    TAX_RATE,
)
from app.store import CartLine, Order, Session, SessionStore

#: A tool receives the caller's session, the shared store, and its own keyword
#: arguments (already parsed from the JSON the model produced).
ToolFn = Callable[..., dict[str, Any]]

MAX_QUANTITY_PER_DISH = 20


# ---------------------------------------------------------------------------
# Internal helpers (not exposed to the model)
# ---------------------------------------------------------------------------

def _money(value: float) -> float:
    """Round to paise so JSON never carries 0.30000000000000004."""
    return round(float(value), 2)


def _public_dish(dish: dict) -> dict[str, Any]:
    """The dish fields worth spending tokens on."""
    return {
        "id": dish["id"],
        "name": dish["name"],
        "category": dish["category"],
        "diet": dish["diet"],
        "price": dish["price"],
        "spice": dish["spice"],
        "description": dish["description"],
    }


def _resolve_dish(name: str) -> tuple[dict | None, list[str]]:
    """Find a dish from free text.

    Customers type "panner tikka" and models echo it verbatim, so an exact
    match is not enough. Returns ``(dish, suggestions)``; when ``dish`` is None
    the suggestions become the ``hint`` the model reads back to the user.
    """
    needle = (name or "").strip().lower()
    if not needle:
        return None, []

    for dish in MENU:
        if dish["name"].lower() == needle or dish["id"].lower() == needle:
            return dish, []

    # Substring match: "biryani" -> the two biryanis, but only if unambiguous.
    partial = [d for d in MENU if needle in d["name"].lower()]
    if len(partial) == 1:
        return partial[0], []
    if len(partial) > 1:
        return None, [d["name"] for d in partial]

    # Last resort: fuzzy match against every dish name, for typos.
    names = [d["name"] for d in MENU]
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
    if len(close) == 1:
        return next(d for d in MENU if d["name"] == close[0]), []
    return None, close


def _zone_for_pincode(pincode: str) -> dict | None:
    """Look up the delivery zone, or None if we do not deliver there."""
    digits = re.sub(r"\D", "", pincode or "")
    if len(digits) != 6:
        return None
    return DELIVERY_ZONES.get(digits[:3])


def _coupon_discount(code: str | None, subtotal: float) -> tuple[float, bool, str | None]:
    """Return ``(discount, free_delivery, label)`` for the applied coupon."""
    if not code:
        return 0.0, False, None
    coupon = COUPONS.get(code.upper())
    if not coupon or not coupon["active"] or subtotal < coupon["min_order"]:
        # The coupon was valid when applied; the cart may have shrunk since.
        return 0.0, False, None

    if coupon["kind"] == "percent":
        raw = subtotal * coupon["value"] / 100
        return _money(min(raw, coupon["max_discount"])), False, coupon["label"]
    if coupon["kind"] == "flat":
        return _money(min(coupon["value"], subtotal)), False, coupon["label"]
    return 0.0, True, coupon["label"]  # free_delivery


def _price_breakdown(session: Session, pincode: str | None) -> dict[str, Any]:
    """The single source of truth for money. Used by calc_total and place_order."""
    subtotal = session.subtotal()
    discount, free_delivery, coupon_label = _coupon_discount(session.coupon_code, subtotal)

    zone = _zone_for_pincode(pincode) if pincode else None
    delivery_fee = 0.0 if (zone is None or free_delivery) else zone["fee"]

    taxable = max(subtotal - discount, 0.0)
    tax = _money(taxable * TAX_RATE)
    total = _money(taxable + delivery_fee + PACKAGING_FEE + tax)

    return {
        "currency": CURRENCY,
        "subtotal": _money(subtotal),
        "coupon_code": session.coupon_code,
        "coupon_label": coupon_label,
        "discount": _money(discount),
        "delivery_fee": _money(delivery_fee),
        # Makes it explicit to the model that a missing pincode means a missing
        # fee, so it knows to ask rather than to quote an incomplete total.
        "delivery_fee_included": zone is not None or free_delivery,
        "delivery_zone": zone["zone"] if zone else None,
        "packaging_fee": _money(PACKAGING_FEE),
        "tax": tax,
        "tax_rate_percent": round(TAX_RATE * 100, 2),
        "total": total,
    }


def _order_view(order: Order, now: datetime | None = None) -> dict[str, Any]:
    """Serialise an order the way both the model and the UI want it.

    Times are stored in UTC but emitted in local time: `get_current_time`
    reports the local clock, and a model shown two different timezones will
    happily tell the customer their food arrived an hour before they ordered it.
    """
    return {
        "order_id": order.order_id,
        "status": order.status(now),
        "placed_at": order.placed_at.astimezone().isoformat(timespec="seconds"),
        "expected_at": order.expected_at().astimezone().isoformat(timespec="seconds"),
        "eta_minutes": order.eta_minutes,
        "items": order.items,
        "totals": order.totals,
        "customer": order.customer,
        "cancel_reason": order.cancel_reason,
    }


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------

def get_menu(session: Session, store: SessionStore, category: str | None = None) -> dict[str, Any]:
    """Tool 1 -- read-only, one optional filter."""
    dishes = MENU
    if category:
        wanted = category.strip().lower()
        dishes = [d for d in MENU if d["category"].lower() == wanted]
        if not dishes:
            known = sorted({d["category"] for d in MENU})
            return {
                "ok": False,
                "error": f"No category named '{category}'.",
                "hint": "Valid categories: " + ", ".join(known),
            }
    return {
        "ok": True,
        "currency": CURRENCY,
        "count": len(dishes),
        "dishes": [_public_dish(d) for d in dishes],
    }


def search_dish(
    session: Session,
    store: SessionStore,
    query: str | None = None,
    diet: str | None = None,
    max_price: float | None = None,
    spice: str | None = None,
) -> dict[str, Any]:
    """Tool 2 -- several optional filters combined with AND."""
    results = MENU

    if query:
        needle = query.strip().lower()
        results = [
            d for d in results
            if needle in d["name"].lower()
            or needle in d["description"].lower()
            or any(needle in tag for tag in d["tags"])
        ]
    if diet:
        wanted = diet.strip().lower()
        if wanted not in {"veg", "nonveg", "vegan"}:
            return {
                "ok": False,
                "error": f"Unknown diet '{diet}'.",
                "hint": "diet must be one of: veg, nonveg, vegan.",
            }
        # Everything vegan is also acceptable to a vegetarian, so widen "veg".
        allowed = {"veg", "vegan"} if wanted == "veg" else {wanted}
        results = [d for d in results if d["diet"] in allowed]
    if max_price is not None:
        results = [d for d in results if d["price"] <= float(max_price)]
    if spice:
        results = [d for d in results if d["spice"] == spice.strip().lower()]

    return {
        "ok": True,
        "currency": CURRENCY,
        "count": len(results),
        "filters_applied": {
            "query": query, "diet": diet, "max_price": max_price, "spice": spice,
        },
        "dishes": [_public_dish(d) for d in results],
        # An empty result is a success, not an error -- but say so plainly, or
        # the model will try to fill the gap from memory.
        "note": None if results else "Nothing on the menu matches those filters.",
    }


def add_to_cart(
    session: Session, store: SessionStore, dish_name: str, quantity: int = 1
) -> dict[str, Any]:
    """Tool 3 -- a write, plus the fuzzy-matching failure path."""
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"quantity '{quantity}' is not a number.",
                "hint": "Pass quantity as an integer, e.g. 2."}
    if quantity < 1 or quantity > MAX_QUANTITY_PER_DISH:
        return {
            "ok": False,
            "error": f"quantity must be between 1 and {MAX_QUANTITY_PER_DISH}.",
            "hint": "For larger orders the customer should call the restaurant.",
        }

    dish, suggestions = _resolve_dish(dish_name)
    if dish is None:
        return {
            "ok": False,
            "error": f"No dish matching '{dish_name}'.",
            "hint": ("Did you mean: " + ", ".join(suggestions) + "?") if suggestions
            else "Call get_menu or search_dish and offer the customer real options.",
        }

    line = session.cart.get(dish["id"])
    if line is None:
        line = CartLine(dish_id=dish["id"], name=dish["name"],
                        unit_price=dish["price"], quantity=0)
        session.cart[dish["id"]] = line
    line.quantity = min(line.quantity + quantity, MAX_QUANTITY_PER_DISH)

    return {
        "ok": True,
        "added": {"name": dish["name"], "quantity": quantity, "unit_price": dish["price"]},
        "line_quantity": line.quantity,
        "cart": session.cart_as_list(),
        "cart_subtotal": session.subtotal(),
        "currency": CURRENCY,
    }


def view_cart(session: Session, store: SessionStore) -> dict[str, Any]:
    """Tool 4 -- the zero-argument case. Its schema still needs a properties block."""
    return {
        "ok": True,
        "currency": CURRENCY,
        "items": session.cart_as_list(),
        "item_count": sum(line.quantity for line in session.cart.values()),
        "subtotal": session.subtotal(),
        "coupon_code": session.coupon_code,
        "note": None if session.cart else "The cart is empty.",
    }


def remove_from_cart(
    session: Session, store: SessionStore, dish_name: str, quantity: int | None = None
) -> dict[str, Any]:
    """Tool 5 -- destructive, so it validates before it deletes."""
    dish, suggestions = _resolve_dish(dish_name)
    if dish is None:
        return {"ok": False, "error": f"No dish matching '{dish_name}'.",
                "hint": ("Did you mean: " + ", ".join(suggestions) + "?") if suggestions
                else "Call view_cart to see what is actually in the cart."}

    line = session.cart.get(dish["id"])
    if line is None:
        return {"ok": False, "error": f"{dish['name']} is not in the cart.",
                "hint": "Call view_cart before removing items."}

    removed = line.quantity if quantity is None else max(int(quantity), 1)
    if removed >= line.quantity:
        removed = line.quantity
        del session.cart[dish["id"]]
    else:
        line.quantity -= removed

    return {
        "ok": True,
        "removed": {"name": dish["name"], "quantity": removed},
        "cart": session.cart_as_list(),
        "cart_subtotal": session.subtotal(),
        "currency": CURRENCY,
    }


def apply_coupon(session: Session, store: SessionStore, code: str) -> dict[str, Any]:
    """Tool 6 -- a tool whose *failures* carry the interesting information."""
    key = (code or "").strip().upper()
    coupon = COUPONS.get(key)

    if coupon is None:
        return {"ok": False, "error": f"'{code}' is not a valid coupon code.",
                "hint": "Active codes are: "
                        + ", ".join(c for c, v in COUPONS.items() if v["active"])}
    if not coupon["active"]:
        return {"ok": False, "error": f"Coupon {key} has expired.",
                "hint": "Offer an active code instead: "
                        + ", ".join(c for c, v in COUPONS.items() if v["active"])}

    subtotal = session.subtotal()
    if subtotal < coupon["min_order"]:
        shortfall = _money(coupon["min_order"] - subtotal)
        return {
            "ok": False,
            "error": f"{key} needs a subtotal of at least {coupon['min_order']}.",
            "hint": f"The cart is {CURRENCY}{shortfall} short. Suggest one more dish.",
            "current_subtotal": subtotal,
        }

    session.coupon_code = key
    discount, free_delivery, label = _coupon_discount(key, subtotal)
    return {
        "ok": True,
        "code": key,
        "label": label,
        "discount": discount,
        "free_delivery": free_delivery,
        "subtotal_after_discount": _money(subtotal - discount),
        "currency": CURRENCY,
    }


def calc_total(
    session: Session, store: SessionStore, pincode: str | None = None
) -> dict[str, Any]:
    """Tool 7 -- arithmetic the model must never attempt itself."""
    if not session.cart:
        return {"ok": False, "error": "The cart is empty, so there is nothing to total.",
                "hint": "Add at least one dish with add_to_cart first."}

    breakdown = _price_breakdown(session, pincode)
    breakdown["ok"] = True
    breakdown["items"] = session.cart_as_list()
    if not breakdown["delivery_fee_included"]:
        breakdown["hint"] = (
            "No serviceable pincode was supplied, so delivery is not in this total. "
            "Ask the customer for their 6-digit pincode."
        )
    return breakdown


def estimate_delivery(session: Session, store: SessionStore, pincode: str) -> dict[str, Any]:
    """Tool 8 -- a derived value with a genuine 'we do not serve you' path."""
    zone = _zone_for_pincode(pincode)
    if zone is None:
        return {
            "ok": False,
            "error": f"We do not deliver to pincode '{pincode}'.",
            "hint": "We currently serve 6-digit pincodes starting 755, 756 or 757.",
        }

    # The kitchen works in parallel, so the wait is the slowest dish, not the sum.
    prep = max((_prep_minutes(line.dish_id) for line in session.cart.values()), default=0)
    return {
        "ok": True,
        "pincode": pincode,
        "zone": zone["zone"],
        "delivery_fee": zone["fee"],
        "travel_minutes": zone["eta_minutes"],
        "kitchen_minutes": prep,
        # Total ETA = slowest dish in the kitchen + time on the road.
        "total_eta_minutes": prep + zone["eta_minutes"],
        "currency": CURRENCY,
    }


def _prep_minutes(dish_id: str) -> int:
    """Kitchen time for a dish id (0 if it somehow left the menu)."""
    dish = next((d for d in MENU if d["id"] == dish_id), None)
    return int(dish["prep_minutes"]) if dish else 0


def place_order(
    session: Session,
    store: SessionStore,
    customer_name: str,
    phone: str,
    address: str,
    pincode: str,
    payment_method: str,
) -> dict[str, Any]:
    """Tool 9 -- five required arguments.

    This is the tool that teaches the model to *stop and ask*. It cannot be
    called until the customer has given a name, phone, address, pincode and
    payment method, so the model has to gather them in conversation first.
    """
    if not session.cart:
        return {"ok": False, "error": "Cannot place an empty order.",
                "hint": "Add dishes with add_to_cart first."}

    digits = re.sub(r"\D", "", phone or "")
    if len(digits) != 10:
        return {"ok": False, "error": f"'{phone}' is not a valid 10-digit phone number.",
                "hint": "Ask the customer to repeat their 10-digit mobile number."}

    method = (payment_method or "").strip().lower().replace(" ", "_")
    if method not in PAYMENT_METHODS:
        return {"ok": False, "error": f"'{payment_method}' is not a supported payment method.",
                "hint": "Supported methods: " + ", ".join(PAYMENT_METHODS)}

    zone = _zone_for_pincode(pincode)
    if zone is None:
        return {"ok": False, "error": f"We do not deliver to pincode '{pincode}'.",
                "hint": "We currently serve pincodes starting 755, 756 or 757."}

    if not (customer_name or "").strip() or len(address.strip()) < 8:
        return {"ok": False, "error": "Name and a full street address are both required.",
                "hint": "Ask for the flat/house number and street, not just the area."}

    breakdown = _price_breakdown(session, pincode)
    prep = max(_prep_minutes(line.dish_id) for line in session.cart.values())

    order = Order(
        order_id=store.next_order_id(),
        placed_at=datetime.now(timezone.utc),
        items=session.cart_as_list(),
        totals={k: v for k, v in breakdown.items() if isinstance(v, (int, float))},
        customer={"name": customer_name.strip(), "phone": digits,
                  "address": address.strip(), "pincode": pincode,
                  "payment_method": method},
        eta_minutes=prep + zone["eta_minutes"],
    )
    session.orders[order.order_id] = order
    session.clear_cart()  # a placed order starts a fresh cart

    return {"ok": True, "order": _order_view(order),
            "message": "Order placed. Quote this order_id to the customer verbatim."}


def order_status(session: Session, store: SessionStore, order_id: str) -> dict[str, Any]:
    """Tool 10 -- lookup by an id the model can only have learned from tool 9."""
    order = session.orders.get((order_id or "").strip().upper())
    if order is None:
        known = list(session.orders)
        return {"ok": False, "error": f"No order with id '{order_id}' in this session.",
                "hint": ("Known order ids: " + ", ".join(known)) if known
                else "No order has been placed in this conversation yet."}
    return {"ok": True, "order": _order_view(order)}


def cancel_order(
    session: Session, store: SessionStore, order_id: str, reason: str | None = None
) -> dict[str, Any]:
    """Tool 11 -- a business rule the model must relay rather than override."""
    order = session.orders.get((order_id or "").strip().upper())
    if order is None:
        return {"ok": False, "error": f"No order with id '{order_id}' in this session.",
                "hint": "Confirm the order id with the customer."}

    if order.cancelled:
        return {"ok": False, "error": f"{order.order_id} was already cancelled.",
                "hint": "Nothing further to do."}

    if not order.is_cancellable():
        return {
            "ok": False,
            "error": f"{order.order_id} is '{order.status()}' and can no longer be cancelled.",
            "hint": "Once the rider has collected the food, only the support desk "
                    "on 1800-BITES can help.",
        }

    order.cancelled = True
    order.cancel_reason = (reason or "").strip() or "No reason given"
    return {"ok": True, "order_id": order.order_id, "status": "cancelled",
            "reason": order.cancel_reason,
            "refund_note": "Prepaid orders are refunded within 3-5 working days."}


def get_current_time(session: Session, store: SessionStore) -> dict[str, Any]:
    """Tool 12 -- grounding. A language model has no clock of its own."""
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
    "add_to_cart": add_to_cart,
    "view_cart": view_cart,
    "remove_from_cart": remove_from_cart,
    "apply_coupon": apply_coupon,
    "calc_total": calc_total,
    "estimate_delivery": estimate_delivery,
    "place_order": place_order,
    "order_status": order_status,
    "cancel_order": cancel_order,
    "get_current_time": get_current_time,
}
