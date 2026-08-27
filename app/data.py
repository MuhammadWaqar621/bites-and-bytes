"""The restaurant's "database".

Kept as plain Python literals on purpose: the point of this project is the
tool-calling loop, not persistence. Every price, coupon and delivery zone the
model is allowed to mention lives here -- if it is not in this file, the model
must not say it.
"""

from __future__ import annotations

CURRENCY = "₹"  # Indian Rupee sign

#: GST applied to the food subtotal (after discount).
TAX_RATE = 0.05

#: Flat packaging charge added to every order.
PACKAGING_FEE = 20.0

#: Menu categories, in the order the UI should render them.
CATEGORIES = [
    "Starters",
    "Mains",
    "Breads",
    "Rice & Biryani",
    "Desserts",
    "Beverages",
]

#: Every dish. `diet` is one of veg / nonveg / vegan and is used by search_dish.
MENU: list[dict] = [
    # --- Starters ---------------------------------------------------------
    {"id": "ST01", "name": "Paneer Tikka", "category": "Starters", "diet": "veg",
     "price": 320.0, "spice": "medium", "prep_minutes": 18,
     "description": "Char-grilled cottage cheese cubes in a smoky yoghurt marinade.",
     "tags": ["grilled", "popular", "protein"]},
    {"id": "ST02", "name": "Chicken 65", "category": "Starters", "diet": "nonveg",
     "price": 340.0, "spice": "hot", "prep_minutes": 20,
     "description": "Crisp fried chicken tossed with curry leaves and dry chillies.",
     "tags": ["fried", "spicy", "popular"]},
    {"id": "ST03", "name": "Crispy Corn", "category": "Starters", "diet": "vegan",
     "price": 240.0, "spice": "mild", "prep_minutes": 12,
     "description": "Golden sweetcorn kernels tossed with pepper and spring onion.",
     "tags": ["fried", "shareable"]},
    {"id": "ST04", "name": "Tandoori Mushroom", "category": "Starters", "diet": "vegan",
     "price": 280.0, "spice": "medium", "prep_minutes": 16,
     "description": "Button mushrooms smoked in the tandoor with ajwain and lime.",
     "tags": ["grilled", "low-calorie"]},

    # --- Mains ------------------------------------------------------------
    {"id": "MN01", "name": "Butter Chicken", "category": "Mains", "diet": "nonveg",
     "price": 480.0, "spice": "mild", "prep_minutes": 25,
     "description": "Tandoori chicken simmered in a tomato, butter and cream gravy.",
     "tags": ["creamy", "signature", "popular"]},
    {"id": "MN02", "name": "Paneer Butter Masala", "category": "Mains", "diet": "veg",
     "price": 420.0, "spice": "mild", "prep_minutes": 22,
     "description": "Cottage cheese in a silky cashew-tomato gravy finished with cream.",
     "tags": ["creamy", "signature"]},
    {"id": "MN03", "name": "Dal Makhani", "category": "Mains", "diet": "veg",
     "price": 320.0, "spice": "mild", "prep_minutes": 30,
     "description": "Black lentils slow-cooked overnight with butter and cream.",
     "tags": ["slow-cooked", "comfort"]},
    {"id": "MN04", "name": "Chana Masala", "category": "Mains", "diet": "vegan",
     "price": 280.0, "spice": "medium", "prep_minutes": 18,
     "description": "Chickpeas in an onion-tomato masala with roasted cumin.",
     "tags": ["protein", "budget"]},
    {"id": "MN05", "name": "Rogan Josh", "category": "Mains", "diet": "nonveg",
     "price": 520.0, "spice": "hot", "prep_minutes": 35,
     "description": "Kashmiri lamb curry built on browned onions and dried chilli.",
     "tags": ["lamb", "premium"]},

    # --- Breads -----------------------------------------------------------
    {"id": "BR01", "name": "Garlic Naan", "category": "Breads", "diet": "veg",
     "price": 80.0, "spice": "mild", "prep_minutes": 8,
     "description": "Leavened flatbread brushed with garlic butter and coriander.",
     "tags": ["popular"]},
    {"id": "BR02", "name": "Butter Roti", "category": "Breads", "diet": "veg",
     "price": 45.0, "spice": "mild", "prep_minutes": 6,
     "description": "Whole-wheat tandoor roti finished with a smear of butter.",
     "tags": ["budget"]},
    {"id": "BR03", "name": "Laccha Paratha", "category": "Breads", "diet": "veg",
     "price": 90.0, "spice": "mild", "prep_minutes": 10,
     "description": "Layered flaky paratha, crisp outside and soft in the middle.",
     "tags": ["flaky"]},

    # --- Rice & Biryani ---------------------------------------------------
    {"id": "RC01", "name": "Hyderabadi Chicken Biryani", "category": "Rice & Biryani",
     "diet": "nonveg", "price": 460.0, "spice": "hot", "prep_minutes": 32,
     "description": "Dum-cooked basmati layered with marinated chicken and saffron.",
     "tags": ["signature", "popular", "one-pot"]},
    {"id": "RC02", "name": "Veg Dum Biryani", "category": "Rice & Biryani", "diet": "veg",
     "price": 380.0, "spice": "medium", "prep_minutes": 30,
     "description": "Seasonal vegetables and basmati sealed and steamed with mint.",
     "tags": ["one-pot"]},
    {"id": "RC03", "name": "Jeera Rice", "category": "Rice & Biryani", "diet": "vegan",
     "price": 180.0, "spice": "mild", "prep_minutes": 12,
     "description": "Basmati tempered with cumin seeds and a neutral oil.",
     "tags": ["side", "budget"]},

    # --- Desserts ---------------------------------------------------------
    {"id": "DS01", "name": "Gulab Jamun", "category": "Desserts", "diet": "veg",
     "price": 140.0, "spice": "mild", "prep_minutes": 5,
     "description": "Two warm milk dumplings soaked in cardamom sugar syrup.",
     "tags": ["sweet", "popular"]},
    {"id": "DS02", "name": "Vegan Mango Sorbet", "category": "Desserts", "diet": "vegan",
     "price": 180.0, "spice": "mild", "prep_minutes": 4,
     "description": "Alphonso mango churned with lime; no dairy at all.",
     "tags": ["sweet", "cold"]},

    # --- Beverages --------------------------------------------------------
    {"id": "BV01", "name": "Masala Chai", "category": "Beverages", "diet": "veg",
     "price": 60.0, "spice": "mild", "prep_minutes": 6,
     "description": "Assam tea boiled with ginger, cardamom and full-fat milk.",
     "tags": ["hot", "budget"]},
    {"id": "BV02", "name": "Sweet Lassi", "category": "Beverages", "diet": "veg",
     "price": 120.0, "spice": "mild", "prep_minutes": 5,
     "description": "Thick churned yoghurt with sugar and a pinch of cardamom.",
     "tags": ["cold"]},
    {"id": "BV03", "name": "Fresh Lime Soda", "category": "Beverages", "diet": "vegan",
     "price": 90.0, "spice": "mild", "prep_minutes": 3,
     "description": "Lime, soda and your choice of salt or sugar, served over ice.",
     "tags": ["cold", "budget"]},
]

#: Discount codes. `kind` decides how the discount is computed:
#:   percent       -> value% off the subtotal, capped at max_discount
#:   flat          -> value rupees off the subtotal
#:   free_delivery -> the delivery fee becomes 0
#: `active=False` exists so the model has to explain a real rejection.
COUPONS: dict[str, dict] = {
    "SAVE20": {"kind": "percent", "value": 20, "min_order": 800.0,
               "max_discount": 300.0, "active": True,
               "label": "20% off orders above 800"},
    "FIRST50": {"kind": "flat", "value": 50, "min_order": 300.0,
                "max_discount": 50.0, "active": True,
                "label": "Flat 50 off your first order"},
    "FREEDEL": {"kind": "free_delivery", "value": 0, "min_order": 500.0,
                "max_discount": 0.0, "active": True,
                "label": "Free delivery on orders above 500"},
    "CHEF10": {"kind": "percent", "value": 10, "min_order": 0.0,
               "max_discount": 150.0, "active": True,
               "label": "10% off, no minimum"},
    "MONSOON25": {"kind": "percent", "value": 25, "min_order": 1500.0,
                  "max_discount": 500.0, "active": False,
                  "label": "Seasonal offer (expired)"},
}

#: Delivery zones keyed by the first three digits of the pincode.
DELIVERY_ZONES: dict[str, dict] = {
    "755": {"zone": "Zone A - City Centre", "fee": 29.0, "eta_minutes": 30},
    "756": {"zone": "Zone B - Riverside", "fee": 49.0, "eta_minutes": 45},
    "757": {"zone": "Zone C - Outer Ring", "fee": 89.0, "eta_minutes": 65},
}

#: Payment methods that place_order will accept.
PAYMENT_METHODS = ["upi", "card", "cash_on_delivery", "wallet"]
