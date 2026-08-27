"""Business constants.

Everything that used to be a hard-coded list here now lives in SQL Server --
see `app/models.py` for the schema and `seed_db.py` for the data. What remains
are the rules that belong in code rather than in rows: the tax rate, the
packaging fee, the order lifecycle and the payment methods the app accepts.
"""

from __future__ import annotations

CURRENCY = "₹"  # Indian Rupee sign

#: GST applied to the food subtotal (after discount).
TAX_RATE = 0.05

#: Flat packaging charge added to every order.
PACKAGING_FEE = 20.0

#: Menu sections, in the order the UI renders them.
CATEGORIES = [
    "Starters",
    "Mains",
    "Breads",
    "Rice & Biryani",
    "Desserts",
    "Beverages",
]

#: Payment methods `place_order` will accept.
PAYMENT_METHODS = ["upi", "card", "cash_on_delivery", "wallet"]

#: Order lifecycle, in progression order.
ORDER_STAGES = ["confirmed", "preparing", "out_for_delivery", "delivered"]

#: Minutes after placement at which each stage begins. Status is derived from
#: this table and the clock, so no background job ever has to update a row.
STAGE_AFTER_MINUTES = {
    "confirmed": 0,
    "preparing": 2,
    "out_for_delivery": 12,
    "delivered": 30,
}

#: Support line quoted when an order can no longer be cancelled.
SUPPORT_NUMBER = "1800-BITES"
