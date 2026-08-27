"""The tool catalogue sent to Azure OpenAI on every request.

This file is the part people underestimate. The model never sees
``app/tools.py`` -- it sees only these descriptions. So the description text is
not documentation, it is *prompt*: it decides whether the model picks the right
tool, fills the right arguments, and knows when to ask a question instead.

Rules used throughout:

* Say **when** to use the tool, not just what it does ("use this before
  quoting any price").
* Use ``enum`` wherever the value set is closed. It is the cheapest possible
  guard against invented arguments.
* Mark a parameter ``required`` only if the tool genuinely cannot run without
  it -- every required field is a question the model must ask the user first.
* ``additionalProperties: false`` stops the model from smuggling in fields the
  Python signature would reject with a TypeError.
"""

from __future__ import annotations

from typing import Any

from app.data import PAYMENT_METHODS


def _fn(name: str, description: str, properties: dict[str, Any],
        required: list[str] | None = None) -> dict[str, Any]:
    """Build one entry of the OpenAI `tools` array."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _fn(
        "get_menu",
        "List the restaurant's dishes with real prices. Call this whenever the "
        "customer asks what is available, or before recommending anything. "
        "Omit `category` to get the whole menu.",
        {
            "category": {
                "type": "string",
                "enum": ["Starters", "Mains", "Breads", "Rice & Biryani",
                         "Desserts", "Beverages"],
                "description": "Restrict the listing to one menu section.",
            }
        },
    ),
    _fn(
        "search_dish",
        "Find dishes by keyword, diet, price ceiling or spice level. Prefer this "
        "over get_menu when the customer states a constraint such as "
        "'vegetarian', 'under 300', 'something spicy' or 'chicken'.",
        {
            "query": {
                "type": "string",
                "description": "Free text matched against dish name, description "
                               "and tags, e.g. 'biryani', 'creamy', 'grilled'.",
            },
            "diet": {
                "type": "string",
                "enum": ["veg", "nonveg", "vegan"],
                "description": "'veg' also returns vegan dishes; 'vegan' is strict.",
            },
            "max_price": {
                "type": "number",
                "description": "Highest acceptable price per dish, in rupees.",
            },
            "spice": {
                "type": "string",
                "enum": ["mild", "medium", "hot"],
                "description": "Heat level the customer asked for.",
            },
        },
    ),
    _fn(
        "add_to_cart",
        "Put a dish into the customer's cart. Use the exact dish name from the "
        "menu. If the tool replies with suggestions, read them back to the "
        "customer instead of guessing which one they meant.",
        {
            "dish_name": {"type": "string",
                          "description": "Dish name exactly as it appears on the menu."},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 20,
                         "description": "How many portions. Defaults to 1."},
        },
        required=["dish_name"],
    ),
    _fn(
        "view_cart",
        "Show what is currently in the cart. Call this before answering any "
        "question about what the customer has ordered so far.",
        {},
    ),
    _fn(
        "remove_from_cart",
        "Take a dish out of the cart. Omit `quantity` to remove the whole line.",
        {
            "dish_name": {"type": "string", "description": "Dish to remove."},
            "quantity": {"type": "integer", "minimum": 1,
                         "description": "How many portions to drop. "
                                        "Omit to remove every portion."},
        },
        required=["dish_name"],
    ),
    _fn(
        "apply_coupon",
        "Apply a discount code to the cart. If it fails, explain the exact "
        "reason returned (expired, minimum not met, unknown code) and offer the "
        "alternatives listed in the hint. Never promise a discount you have not "
        "successfully applied here.",
        {"code": {"type": "string",
                  "description": "Coupon code, e.g. SAVE20. Case-insensitive."}},
        required=["code"],
    ),
    _fn(
        "calc_total",
        "Compute the bill: subtotal, coupon discount, delivery fee, packaging "
        "and 5% tax. You must call this before stating any total -- never add "
        "the numbers up yourself. Pass `pincode` so the delivery fee is included.",
        {"pincode": {"type": "string",
                     "description": "Customer's 6-digit delivery pincode."}},
    ),
    _fn(
        "estimate_delivery",
        "Check whether an address is serviceable and how long delivery will "
        "take, combining kitchen prep time with travel time for that zone.",
        {"pincode": {"type": "string", "description": "6-digit delivery pincode."}},
        required=["pincode"],
    ),
    _fn(
        "place_order",
        "Confirm the order. Every argument is mandatory: if you are missing any "
        "of them, ASK THE CUSTOMER FOR IT AND DO NOT CALL THIS TOOL YET. Never "
        "invent a name, phone number or address. The order id comes back in the "
        "response -- quote that id, never one you made up.",
        {
            "customer_name": {"type": "string", "description": "Full name of the customer."},
            "phone": {"type": "string", "description": "10-digit mobile number."},
            "address": {"type": "string",
                        "description": "Flat/house number and street, not just the area."},
            "pincode": {"type": "string", "description": "6-digit delivery pincode."},
            "payment_method": {
                "type": "string",
                "enum": PAYMENT_METHODS,
                "description": "How the customer will pay.",
            },
        },
        required=["customer_name", "phone", "address", "pincode", "payment_method"],
    ),
    _fn(
        "order_status",
        "Look up a placed order and report its live stage (confirmed, preparing, "
        "out_for_delivery, delivered or cancelled). Only use an order id that "
        "came from place_order in this conversation.",
        {"order_id": {"type": "string", "description": "Order id such as ORD-1042."}},
        required=["order_id"],
    ),
    _fn(
        "cancel_order",
        "Cancel a placed order. This is refused once the order is out for "
        "delivery -- if that happens, relay the refusal and the support number "
        "rather than trying again.",
        {
            "order_id": {"type": "string", "description": "Order id to cancel."},
            "reason": {"type": "string",
                       "description": "Why the customer is cancelling, if they said."},
        },
        required=["order_id"],
    ),
    _fn(
        "get_current_time",
        "Get the current local date, time and whether the kitchen is open. You "
        "have no clock of your own, so call this for any question about 'now', "
        "'today', 'tonight' or opening hours.",
        {},
    ),
]


def tool_catalogue() -> list[dict[str, Any]]:
    """A flattened view of the schemas for the UI's "Tools" panel.

    Derived from TOOL_SCHEMAS rather than hand-written, so the panel can never
    drift out of sync with what the model is actually offered.
    """
    catalogue = []
    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        params = fn["parameters"]
        catalogue.append({
            "name": fn["name"],
            "description": fn["description"],
            "parameters": [
                {
                    "name": param,
                    "type": spec.get("type", "string"),
                    "required": param in params["required"],
                    "enum": spec.get("enum"),
                }
                for param, spec in params["properties"].items()
            ],
        })
    return catalogue
