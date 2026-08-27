"""Tool-level tests. No network, no tokens, no LLM.

This is the payoff of keeping tools as plain functions: the entire business
layer is verifiable in milliseconds, so when the assistant misbehaves you
already know the bug is in the prompt or the schema, not in the maths.
"""

from __future__ import annotations

import pytest

from app import tools
from app.store import Session, SessionStore


@pytest.fixture()
def ctx():
    """A blank session plus the store the tools need for order ids."""
    return Session(session_id="test"), SessionStore()


# --- discovery -------------------------------------------------------------

def test_get_menu_rejects_unknown_category(ctx):
    session, store = ctx
    result = tools.get_menu(session, store, category="Sushi")
    assert result["ok"] is False
    assert "Starters" in result["hint"]


def test_search_treats_vegan_dishes_as_vegetarian_friendly(ctx):
    session, store = ctx
    names = {d["name"] for d in tools.search_dish(session, store, diet="veg")["dishes"]}
    assert "Crispy Corn" in names          # vegan, still fine for a vegetarian
    assert "Butter Chicken" not in names


def test_search_combines_filters(ctx):
    session, store = ctx
    result = tools.search_dish(session, store, diet="vegan", max_price=200)
    assert result["ok"] is True
    assert all(d["price"] <= 200 and d["diet"] == "vegan" for d in result["dishes"])


def test_empty_search_is_a_success_with_a_note(ctx):
    session, store = ctx
    result = tools.search_dish(session, store, query="pizza")
    assert result["ok"] is True and result["count"] == 0
    assert result["note"]


# --- cart ------------------------------------------------------------------

def test_add_to_cart_accumulates_quantity(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Paneer Tikka", quantity=2)
    result = tools.add_to_cart(session, store, dish_name="Paneer Tikka", quantity=1)
    assert result["line_quantity"] == 3
    assert session.subtotal() == 960.0


def test_unambiguous_typo_is_auto_corrected(ctx):
    # Models echo the customer's spelling verbatim, so a single close match is
    # resolved silently rather than bounced back as an error.
    session, store = ctx
    result = tools.add_to_cart(session, store, dish_name="panner tika")
    assert result["ok"] is True
    assert result["added"]["name"] == "Paneer Tikka"


def test_dish_that_is_not_on_the_menu_is_refused(ctx):
    session, store = ctx
    result = tools.add_to_cart(session, store, dish_name="Margherita Pizza")
    assert result["ok"] is False
    assert "get_menu" in result["hint"]


def test_ambiguous_name_asks_instead_of_guessing(ctx):
    session, store = ctx
    result = tools.add_to_cart(session, store, dish_name="biryani")
    assert result["ok"] is False
    assert "Hyderabadi Chicken Biryani" in result["hint"]


def test_remove_without_quantity_drops_the_whole_line(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Garlic Naan", quantity=3)
    tools.remove_from_cart(session, store, dish_name="Garlic Naan")
    assert session.cart == {}


# --- coupons and money -----------------------------------------------------

def test_coupon_below_minimum_reports_the_shortfall(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Masala Chai")  # 60
    result = tools.apply_coupon(session, store, code="SAVE20")
    assert result["ok"] is False
    assert "740" in result["hint"]  # 800 minimum - 60 subtotal


def test_expired_coupon_is_rejected_with_alternatives(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Rogan Josh", quantity=4)
    result = tools.apply_coupon(session, store, code="MONSOON25")
    assert result["ok"] is False and "expired" in result["error"].lower()
    assert "SAVE20" in result["hint"]


def test_percentage_discount_is_capped(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Rogan Josh", quantity=5)  # 2600
    result = tools.apply_coupon(session, store, code="SAVE20")
    assert result["discount"] == 300.0  # 20% would be 520, cap is 300


def test_total_is_subtotal_minus_discount_plus_fees_and_tax(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Paneer Butter Masala", quantity=2)  # 840
    tools.apply_coupon(session, store, code="SAVE20")                                # -168
    total = tools.calc_total(session, store, pincode="755001")

    assert total["subtotal"] == 840.0
    assert total["discount"] == 168.0
    assert total["delivery_fee"] == 29.0
    assert total["tax"] == pytest.approx(33.6)          # 5% of 672
    assert total["total"] == pytest.approx(754.6)       # 672 + 29 + 20 + 33.6


def test_free_delivery_coupon_zeroes_the_fee(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Butter Chicken", quantity=2)
    tools.apply_coupon(session, store, code="FREEDEL")
    assert tools.calc_total(session, store, pincode="757001")["delivery_fee"] == 0.0


def test_total_without_pincode_flags_the_missing_fee(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Dal Makhani")
    result = tools.calc_total(session, store)
    assert result["delivery_fee_included"] is False
    assert "pincode" in result["hint"]


# --- delivery --------------------------------------------------------------

def test_unserviceable_pincode_is_a_clean_refusal(ctx):
    session, store = ctx
    result = tools.estimate_delivery(session, store, pincode="110001")
    assert result["ok"] is False and "755" in result["hint"]


def test_eta_is_slowest_dish_plus_travel(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Masala Chai")        # 6 min
    tools.add_to_cart(session, store, dish_name="Rogan Josh")         # 35 min
    result = tools.estimate_delivery(session, store, pincode="755001")  # 30 min travel
    assert result["total_eta_minutes"] == 65


# --- ordering --------------------------------------------------------------

def _place(session, store, **overrides):
    payload = {
        "customer_name": "Waqar", "phone": "9876543210",
        "address": "Flat 4B, Nehru Street", "pincode": "755001",
        "payment_method": "upi",
    }
    payload.update(overrides)
    return tools.place_order(session, store, **payload)


def test_cannot_order_an_empty_cart(ctx):
    session, store = ctx
    assert _place(session, store)["ok"] is False


def test_place_order_validates_phone_and_payment(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Veg Dum Biryani")
    assert _place(session, store, phone="12345")["ok"] is False
    assert _place(session, store, payment_method="bitcoin")["ok"] is False


def test_successful_order_clears_the_cart_and_returns_an_id(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Veg Dum Biryani", quantity=2)
    result = _place(session, store)

    assert result["ok"] is True
    assert result["order"]["order_id"].startswith("ORD-")
    assert session.cart == {}
    assert result["order"]["status"] == "confirmed"


def test_order_status_rejects_an_id_from_nowhere(ctx):
    session, store = ctx
    result = tools.order_status(session, store, order_id="ORD-9999")
    assert result["ok"] is False


def test_cancel_is_refused_once_out_for_delivery(ctx):
    from datetime import timedelta

    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Chana Masala")
    order_id = _place(session, store)["order"]["order_id"]

    # Rewind the clock by 20 minutes: past the out_for_delivery threshold.
    order = session.orders[order_id]
    order.placed_at -= timedelta(minutes=20)

    result = tools.cancel_order(session, store, order_id=order_id)
    assert result["ok"] is False
    assert "1800-BITES" in result["hint"]


def test_cancel_succeeds_while_still_in_the_kitchen(ctx):
    session, store = ctx
    tools.add_to_cart(session, store, dish_name="Chana Masala")
    order_id = _place(session, store)["order"]["order_id"]

    result = tools.cancel_order(session, store, order_id=order_id, reason="Changed my mind")
    assert result["ok"] is True
    assert session.orders[order_id].status() == "cancelled"


# --- grounding -------------------------------------------------------------

def test_current_time_reports_kitchen_hours(ctx):
    session, store = ctx
    result = tools.get_current_time(session, store)
    assert result["ok"] is True
    assert isinstance(result["kitchen_open"], bool)
