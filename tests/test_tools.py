"""Tool-level tests. No network, no tokens, no LLM, no SQL Server.

This is the payoff of keeping tools thin and routing data access through the
repository: the entire business layer is verifiable in a second, so when the
assistant misbehaves you already know the bug is in the prompt or the schema,
not in the maths.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app import repository as repo
from app import tools


# --- discovery -------------------------------------------------------------

def test_get_menu_rejects_unknown_category(ctx):
    result = tools.get_menu(ctx, category="Sushi")
    assert result["ok"] is False
    assert "Starters" in result["hint"]


def test_search_treats_vegan_dishes_as_vegetarian_friendly(ctx):
    names = {d["name"] for d in tools.search_dish(ctx, diet="veg")["dishes"]}
    assert "Crispy Corn" in names          # vegan, still fine for a vegetarian
    assert "Butter Chicken" not in names


def test_search_combines_filters(ctx):
    result = tools.search_dish(ctx, diet="vegan", max_price=300)
    assert result["ok"] is True
    assert all(d["price"] <= 300 and d["diet"] == "vegan" for d in result["dishes"])


def test_empty_search_is_a_success_with_a_note(ctx):
    result = tools.search_dish(ctx, query="pizza")
    assert result["ok"] is True and result["total_matches"] == 0
    assert result["note"]


def test_unknown_diet_is_rejected(ctx):
    assert tools.search_dish(ctx, diet="carnivore")["ok"] is False


def test_popular_dishes_ranks_by_units_sold(ctx):
    from app.models import Dish

    ctx.db.query(Dish).filter(Dish.name == "Garlic Naan").update({"times_ordered": 900})
    ctx.db.query(Dish).filter(Dish.name == "Masala Chai").update({"times_ordered": 400})
    ctx.db.commit()

    dishes = tools.popular_dishes(ctx, limit=2)["dishes"]
    assert [d["name"] for d in dishes] == ["Garlic Naan", "Masala Chai"]


# --- cart ------------------------------------------------------------------

def test_add_to_cart_accumulates_quantity(ctx):
    tools.add_to_cart(ctx, dish_name="Paneer Tikka", quantity=2)
    result = tools.add_to_cart(ctx, dish_name="Paneer Tikka", quantity=1)
    assert result["line_quantity"] == 3
    assert result["cart_subtotal"] == 960.0


def test_unambiguous_typo_is_auto_corrected(ctx):
    # Models echo the customer's spelling verbatim, so a single close match is
    # resolved silently rather than bounced back as an error.
    result = tools.add_to_cart(ctx, dish_name="panner tika")
    assert result["ok"] is True
    assert result["added"]["name"] == "Paneer Tikka"


def test_ambiguous_name_asks_instead_of_guessing(ctx):
    result = tools.add_to_cart(ctx, dish_name="biryani")
    assert result["ok"] is False
    assert "Hyderabadi Chicken Biryani" in result["hint"]


def test_dish_that_is_not_on_the_menu_is_refused(ctx):
    result = tools.add_to_cart(ctx, dish_name="Margherita Pizza")
    assert result["ok"] is False
    assert "search_dish" in result["hint"]


def test_remove_without_quantity_drops_the_whole_line(ctx):
    tools.add_to_cart(ctx, dish_name="Garlic Naan", quantity=3)
    tools.remove_from_cart(ctx, dish_name="Garlic Naan")
    assert tools.view_cart(ctx)["items"] == []


def test_carts_are_isolated_per_conversation(ctx, user):
    tools.add_to_cart(ctx, dish_name="Garlic Naan", quantity=2)

    second = repo.create_conversation(ctx.db, user, "Another chat")
    ctx.db.commit()
    other = tools.ToolContext(ctx.db, user, second)

    assert tools.view_cart(other)["items"] == []
    assert tools.view_cart(ctx)["item_count"] == 2


# --- coupons and money -----------------------------------------------------

def test_coupon_below_minimum_reports_the_shortfall(ctx):
    tools.add_to_cart(ctx, dish_name="Masala Chai")  # 60
    result = tools.apply_coupon(ctx, code="SAVE20")
    assert result["ok"] is False
    assert "740" in result["hint"]  # 800 minimum - 60 subtotal


def test_expired_coupon_is_rejected_with_alternatives(ctx):
    tools.add_to_cart(ctx, dish_name="Rogan Josh", quantity=4)
    result = tools.apply_coupon(ctx, code="MONSOON25")
    assert result["ok"] is False and "expired" in result["error"].lower()
    assert "SAVE20" in result["hint"]


def test_percentage_discount_is_capped(ctx):
    tools.add_to_cart(ctx, dish_name="Rogan Josh", quantity=5)  # 2600
    result = tools.apply_coupon(ctx, code="SAVE20")
    assert result["discount"] == 300.0  # 20% would be 520, cap is 300


def test_total_is_subtotal_minus_discount_plus_fees_and_tax(ctx):
    tools.add_to_cart(ctx, dish_name="Paneer Butter Masala", quantity=2)  # 840
    tools.apply_coupon(ctx, code="SAVE20")                                # -168
    total = tools.calc_total(ctx, pincode="755001")

    assert total["subtotal"] == 840.0
    assert total["discount"] == 168.0
    assert total["delivery_fee"] == 29.0
    assert total["tax"] == pytest.approx(33.6)      # 5% of 672
    assert total["total"] == pytest.approx(754.6)   # 672 + 29 + 20 + 33.6


def test_free_delivery_coupon_zeroes_the_fee(ctx):
    tools.add_to_cart(ctx, dish_name="Butter Chicken", quantity=2)
    tools.apply_coupon(ctx, code="FREEDEL")
    assert tools.calc_total(ctx, pincode="757001")["delivery_fee"] == 0.0


def test_total_without_pincode_flags_the_missing_fee(ctx):
    tools.add_to_cart(ctx, dish_name="Chana Masala")
    result = tools.calc_total(ctx)
    assert result["delivery_fee_included"] is False
    assert "pincode" in result["hint"]


def test_total_of_an_empty_cart_is_a_clean_refusal(ctx):
    assert tools.calc_total(ctx)["ok"] is False


# --- delivery --------------------------------------------------------------

def test_unserviceable_pincode_is_a_clean_refusal(ctx):
    result = tools.estimate_delivery(ctx, pincode="110001")
    assert result["ok"] is False and "755" in result["hint"]


def test_eta_is_slowest_dish_plus_travel(ctx):
    tools.add_to_cart(ctx, dish_name="Masala Chai")   # 6 min
    tools.add_to_cart(ctx, dish_name="Rogan Josh")    # 35 min
    result = tools.estimate_delivery(ctx, pincode="755001")  # 30 min travel
    assert result["total_eta_minutes"] == 65


# --- ordering --------------------------------------------------------------

def _place(ctx, **overrides):
    payload = {
        "customer_name": "Waqar", "phone": "9876543210",
        "address": "Flat 4B, Nehru Street", "pincode": "755001",
        "payment_method": "upi",
    }
    payload.update(overrides)
    return tools.place_order(ctx, **payload)


def test_cannot_order_an_empty_cart(ctx):
    assert _place(ctx)["ok"] is False


def test_place_order_validates_phone_and_payment(ctx):
    tools.add_to_cart(ctx, dish_name="Veg Dum Biryani")
    assert _place(ctx, phone="12345")["ok"] is False
    assert _place(ctx, payment_method="bitcoin")["ok"] is False


def test_successful_order_clears_the_cart_and_returns_an_id(ctx):
    tools.add_to_cart(ctx, dish_name="Veg Dum Biryani", quantity=2)
    result = _place(ctx)

    assert result["ok"] is True
    assert result["order"]["order_id"].startswith("ORD-")
    assert result["order"]["status"] == "confirmed"
    assert tools.view_cart(ctx)["items"] == []


def test_placing_an_order_increments_dish_popularity(ctx):
    from app.models import Dish

    tools.add_to_cart(ctx, dish_name="Garlic Naan", quantity=4)
    _place(ctx)
    naan = ctx.db.query(Dish).filter(Dish.name == "Garlic Naan").one()
    assert naan.times_ordered == 4


def test_order_status_rejects_an_id_from_nowhere(ctx):
    assert tools.order_status(ctx, order_id="ORD-9999")["ok"] is False


def test_cancel_is_refused_once_out_for_delivery(ctx):
    tools.add_to_cart(ctx, dish_name="Chana Masala")
    order_id = _place(ctx)["order"]["order_id"]

    # Rewind the clock by 20 minutes: past the out_for_delivery threshold.
    order = repo.get_order(ctx.db, ctx.user, order_id)
    order.placed_at -= timedelta(minutes=20)
    ctx.db.commit()

    result = tools.cancel_order(ctx, order_id=order_id)
    assert result["ok"] is False
    assert "1800-BITES" in result["hint"]


def test_cancel_succeeds_while_still_in_the_kitchen(ctx):
    tools.add_to_cart(ctx, dish_name="Chana Masala")
    order_id = _place(ctx)["order"]["order_id"]

    result = tools.cancel_order(ctx, order_id=order_id, reason="Changed my mind")
    assert result["ok"] is True
    assert tools.order_status(ctx, order_id=order_id)["order"]["status"] == "cancelled"


# --- history, memory and isolation -----------------------------------------

def test_find_past_orders_returns_this_users_orders(ctx):
    tools.add_to_cart(ctx, dish_name="Butter Chicken")
    _place(ctx)
    result = tools.find_past_orders(ctx)
    assert result["count"] == 1
    assert result["orders"][0]["items"][0]["name"] == "Butter Chicken"


def test_profile_offers_the_last_delivery_details(ctx):
    tools.add_to_cart(ctx, dish_name="Butter Chicken", quantity=2)
    _place(ctx)

    profile = tools.get_my_profile(ctx)
    assert profile["order_count"] == 1
    assert profile["saved_delivery"]["address"] == "Flat 4B, Nehru Street"
    assert profile["usual_dishes"][0] == {"name": "Butter Chicken", "times_ordered": 2}


def test_profile_of_a_brand_new_customer_has_nothing_saved(ctx):
    profile = tools.get_my_profile(ctx)
    assert profile["order_count"] == 0
    assert profile["saved_delivery"] is None
    assert "ask for the delivery details" in profile["hint"]


def test_optional_signup_details_are_offered_before_any_order(db):
    """Fields typed at sign-up stand in until there is a real order to learn from."""
    person = repo.create_user(db, "new@bitesbytes.app", "supersecret9",
                              display_name="New Customer", phone="9876500000",
                              address="12 Palm Grove", pincode="755002")
    conversation = repo.create_conversation(db, person, "First chat")
    db.commit()

    profile = tools.get_my_profile(tools.ToolContext(db, person, conversation))
    assert profile["saved_delivery"]["address"] == "12 Palm Grove"
    assert profile["saved_delivery"]["source"] == "their sign-up details"
    # Never invented: no order has been placed, so there is no payment method.
    assert profile["saved_delivery"]["payment_method"] is None


def test_blank_signup_fields_are_stored_as_null_not_empty_strings(db):
    person = repo.create_user(db, "blank@bitesbytes.app", "supersecret9",
                              display_name="  ", phone="  ", address="")
    db.commit()

    assert person.default_phone is None
    assert person.default_address is None
    # A blank name falls back to the local part of the email.
    assert person.display_name == "blank"


def test_one_customer_cannot_read_anothers_order(ctx, other_ctx):
    """The authorisation check that matters most.

    A model that hallucinates a valid order id belonging to someone else must
    get 'not found', never a stranger's address and phone number.
    """
    tools.add_to_cart(ctx, dish_name="Butter Chicken")
    order_id = _place(ctx)["order"]["order_id"]

    assert tools.order_status(other_ctx, order_id=order_id)["ok"] is False
    assert tools.cancel_order(other_ctx, order_id=order_id)["ok"] is False
    assert tools.find_past_orders(other_ctx)["count"] == 0


# --- analytics -------------------------------------------------------------

def test_spend_summary_counts_only_this_period(ctx):
    from app.models import Order

    tools.add_to_cart(ctx, dish_name="Butter Chicken")   # 480
    _place(ctx)
    tools.add_to_cart(ctx, dish_name="Masala Chai")      # 60
    old = _place(ctx)["order"]["order_id"]

    # Push the second order back two months.
    repo.get_order(ctx.db, ctx.user, old).placed_at -= timedelta(days=62)
    ctx.db.commit()

    month = tools.get_spend_summary(ctx, period="this_month")
    assert month["order_count"] == 1
    assert month["total_spend"] == pytest.approx(553.0)   # 480 + 29 + 20 + 24

    everything = tools.get_spend_summary(ctx, period="all_time")
    assert everything["order_count"] == 2


def test_spend_summary_excludes_cancelled_orders(ctx):
    tools.add_to_cart(ctx, dish_name="Butter Chicken")
    order_id = _place(ctx)["order"]["order_id"]
    tools.cancel_order(ctx, order_id=order_id)

    summary = tools.get_spend_summary(ctx, period="all_time")
    assert summary["order_count"] == 0
    assert summary["total_spend"] == 0.0
    assert summary["cancelled_count"] == 1


def test_spend_summary_rejects_an_unknown_period(ctx):
    result = tools.get_spend_summary(ctx, period="last_fortnight")
    assert result["ok"] is False
    assert "this_month" in result["hint"]


def test_summary_returns_stat_tiles_not_a_chart(ctx):
    # A handful of headline numbers is a KPI row; a one-bar bar chart would be
    # slower to read and says nothing extra.
    chart = tools.get_spend_summary(ctx, period="all_time")["charts"][0]
    assert chart["kind"] == "kpi"
    assert [tile["label"] for tile in chart["tiles"]][:2] == ["Total spend", "Orders"]


def test_order_trend_buckets_by_month_and_ends_with_this_one(ctx):
    tools.add_to_cart(ctx, dish_name="Veg Dum Biryani")
    _place(ctx)

    trend = tools.get_order_trend(ctx, group_by="month", periods=3)
    assert len(trend["series"]) == 3
    assert trend["series"][-1]["orders"] == 1      # this month
    assert trend["series"][0]["orders"] == 0       # two months ago
    assert trend["total_orders"] == 1


def test_order_trend_never_puts_two_units_on_one_chart(ctx):
    """Spend and order counts are different units, so they get separate charts.

    A dual-axis chart is the single most misread form there is; this tool is
    built so it cannot produce one.
    """
    charts = tools.get_order_trend(ctx, group_by="week", periods=4)["charts"]
    assert len(charts) == 2
    assert {c["measure"] for c in charts} == {"spend", "orders"}
    assert all(c["kind"] == "line" for c in charts)


def test_order_trend_clamps_a_silly_bucket_count(ctx):
    assert len(tools.get_order_trend(ctx, group_by="day", periods=999)["series"]) == 24
    assert len(tools.get_order_trend(ctx, group_by="day", periods=1)["series"]) == 2


def test_order_trend_rejects_unknown_granularity(ctx):
    assert tools.get_order_trend(ctx, group_by="hour")["ok"] is False


def test_spend_breakdown_ranks_categories_by_money(ctx):
    tools.add_to_cart(ctx, dish_name="Rogan Josh", quantity=2)   # Mains, 1040
    tools.add_to_cart(ctx, dish_name="Garlic Naan")              # Breads, 80
    _place(ctx)

    result = tools.get_spend_breakdown(ctx, dimension="category")
    names = [row["name"] for row in result["rows"]]
    assert names[0] == "Mains"
    assert "Breads" in names

    chart = result["charts"][0]
    assert chart["kind"] == "bar"
    assert chart["points"][0]["value"] > chart["points"][1]["value"]


def test_spend_breakdown_by_dish_and_payment_method(ctx):
    tools.add_to_cart(ctx, dish_name="Butter Chicken", quantity=3)
    _place(ctx, payment_method="wallet")

    by_dish = tools.get_spend_breakdown(ctx, dimension="dish")
    assert by_dish["rows"][0]["name"] == "Butter Chicken"
    assert by_dish["rows"][0]["units"] == 3

    by_payment = tools.get_spend_breakdown(ctx, dimension="payment_method")
    assert by_payment["rows"][0]["name"] == "wallet"


def test_spend_breakdown_with_no_orders_says_so(ctx):
    result = tools.get_spend_breakdown(ctx, dimension="category")
    assert result["ok"] is True and result["rows"] == []
    assert "nothing to break down" in result["note"]


def test_spend_breakdown_rejects_an_unknown_dimension(ctx):
    assert tools.get_spend_breakdown(ctx, dimension="colour")["ok"] is False


def test_analytics_are_scoped_to_the_signed_in_customer(ctx, other_ctx):
    tools.add_to_cart(ctx, dish_name="Rogan Josh", quantity=2)
    _place(ctx)

    assert tools.get_spend_summary(other_ctx, period="all_time")["total_spend"] == 0.0
    assert tools.get_order_trend(other_ctx, group_by="month")["total_orders"] == 0
    assert tools.get_spend_breakdown(other_ctx, dimension="category")["rows"] == []


# --- grounding -------------------------------------------------------------

def test_current_time_reports_kitchen_hours(ctx):
    result = tools.get_current_time(ctx)
    assert result["ok"] is True
    assert isinstance(result["kitchen_open"], bool)


# --- the contract between schemas and code ---------------------------------

def test_every_schema_has_a_matching_registry_entry():
    from app.schemas import TOOL_SCHEMAS

    declared = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert declared == set(tools.TOOL_REGISTRY)


def test_every_required_argument_exists_on_the_python_function():
    import inspect

    from app.schemas import TOOL_SCHEMAS

    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        signature = inspect.signature(tools.TOOL_REGISTRY[fn["name"]])
        for param in fn["parameters"]["properties"]:
            assert param in signature.parameters, f"{fn['name']}.{param}"
