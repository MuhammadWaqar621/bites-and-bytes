"""Database bootstrap: create it, build the schema, fill it with data.

Called automatically by `run.py`, so `python run.py` is genuinely the only
command anyone has to type. It is idempotent -- a second run finds the tables
populated and does nothing.

The generator is seeded with a fixed value, so the same 48,000-odd rows come
out every time. That matters more than it sounds: a demo whose "best seller"
changes on every rebuild is a demo you cannot write a README about.

Volumes it produces:

======================  ========
dishes                       238
coupons                       14
delivery_zones                60
users                        201
orders                    12,000
order_items              ~36,000
conversations + messages     ~35
======================  ========
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.data import PACKAGING_FEE, PAYMENT_METHODS, TAX_RATE
from app.db import Base, SessionLocal, engine
from app.repository import order_code_for
from app.models import (
    ChatMessage,
    Conversation,
    Coupon,
    DeliveryZone,
    Dish,
    Order,
    OrderItem,
    User,
    utcnow,
)
from app.security import hash_password

RNG_SEED = 20260827

DEMO_EMAIL = "demo@bitesbytes.app"
DEMO_PASSWORD = "demo12345"

# ---------------------------------------------------------------------------
# Menu generation
# ---------------------------------------------------------------------------
# Dishes are combinations rather than 238 hand-typed rows: a protein crossed
# with a cooking style is exactly how a real Indian menu is built, and it gives
# the search tool a realistically large catalogue to filter.

STARTER_PROTEINS = [
    ("Paneer", "veg", 1.00), ("Chicken", "nonveg", 1.05), ("Mushroom", "vegan", 0.85),
    ("Corn", "vegan", 0.70), ("Fish", "nonveg", 1.25), ("Prawn", "nonveg", 1.45),
    ("Soya Chaap", "vegan", 0.80), ("Cauliflower", "vegan", 0.75),
    ("Broccoli", "vegan", 0.90), ("Lamb", "nonveg", 1.50),
]
STARTER_STYLES = [
    ("Tikka", "medium", 18, 320, "Char-grilled in a smoky yoghurt marinade."),
    ("65", "hot", 20, 340, "Crisp-fried with curry leaves and dry red chillies."),
    ("Manchurian", "medium", 16, 300, "Tossed in a tangy Indo-Chinese garlic sauce."),
    ("Pakora", "mild", 12, 260, "Dipped in spiced gram flour and fried golden."),
    ("Seekh Kebab", "hot", 22, 380, "Minced, skewered and finished over charcoal."),
    ("Malai Tikka", "mild", 18, 350, "Marinated in cream, cheese and white pepper."),
    ("Chilli", "hot", 15, 310, "Wok-tossed with peppers, onion and green chilli."),
    ("Tandoori", "medium", 20, 360, "Smoked in the clay oven with ajwain and lime."),
]

MAIN_PROTEINS = [
    ("Paneer", "veg", 1.00), ("Chicken", "nonveg", 1.10), ("Lamb", "nonveg", 1.45),
    ("Mushroom", "vegan", 0.85), ("Chickpea", "vegan", 0.75), ("Egg", "veg", 0.80),
    ("Prawn", "nonveg", 1.40), ("Kofta", "veg", 0.95),
]
MAIN_GRAVIES = [
    ("Butter Masala", "mild", 22, 420, "In a silky cashew-tomato gravy finished with cream."),
    ("Tikka Masala", "medium", 24, 440, "Grilled first, then simmered in a spiced tomato gravy."),
    ("Kadai", "hot", 22, 430, "Cooked with crushed coriander seeds and bell peppers."),
    ("Korma", "mild", 26, 450, "A mild, fragrant gravy of cashews, yoghurt and saffron."),
    ("Vindaloo", "hot", 25, 460, "Goan-style, sharp with vinegar and Kashmiri chilli."),
    ("Rogan Josh", "hot", 30, 480, "Kashmiri, built on browned onions and dried chilli."),
    ("Do Pyaza", "medium", 22, 420, "Twice the onion, cooked down to a thick masala."),
    ("Saagwala", "mild", 24, 410, "Folded through slow-cooked spinach and fenugreek."),
    ("Jalfrezi", "hot", 21, 430, "Stir-fried with peppers, tomato and green chilli."),
    ("Malai", "mild", 23, 440, "A gentle white gravy of cream, cashew and cardamom."),
]

CLASSIC_MAINS = [
    ("Dal Makhani", "veg", 320, "mild", 30, "Black lentils slow-cooked overnight with butter and cream."),
    ("Dal Tadka", "veg", 260, "medium", 20, "Yellow lentils finished with a sizzling cumin tempering."),
    ("Chana Masala", "vegan", 280, "medium", 18, "Chickpeas in an onion-tomato masala with roasted cumin."),
    ("Rajma Masala", "vegan", 290, "medium", 25, "Red kidney beans simmered in a thick Punjabi gravy."),
    ("Baingan Bharta", "vegan", 300, "medium", 22, "Fire-roasted aubergine mashed with onion and tomato."),
    ("Aloo Gobi", "vegan", 270, "mild", 18, "Potato and cauliflower turned through turmeric and cumin."),
    ("Bhindi Masala", "vegan", 280, "medium", 20, "Okra cooked dry with onion, amchur and coriander."),
    ("Malai Kofta", "veg", 380, "mild", 26, "Potato-paneer dumplings in a rich cashew gravy."),
]

BREADS = [
    ("Plain Naan", 60, 7), ("Butter Naan", 70, 7), ("Garlic Naan", 80, 8),
    ("Cheese Naan", 110, 9), ("Chilli Cheese Naan", 120, 9), ("Peshawari Naan", 115, 10),
    ("Keema Naan", 140, 12), ("Tandoori Roti", 40, 6), ("Butter Roti", 45, 6),
    ("Laccha Paratha", 90, 10), ("Aloo Paratha", 100, 12), ("Pudina Paratha", 95, 10),
    ("Amritsari Kulcha", 105, 11), ("Onion Kulcha", 95, 10),
]

BIRYANI_STYLES = [("Hyderabadi", 1.10), ("Lucknowi", 1.05), ("Kolkata", 1.00)]
BIRYANI_PROTEINS = [
    ("Chicken", "nonveg", 460), ("Mutton", "nonveg", 560), ("Veg", "veg", 380),
    ("Prawn", "nonveg", 540), ("Egg", "veg", 360), ("Paneer", "veg", 420),
]
RICE_SIDES = [
    ("Jeera Rice", "vegan", 180, 12), ("Steamed Basmati", "vegan", 140, 10),
    ("Peas Pulao", "vegan", 200, 14), ("Kashmiri Pulao", "veg", 240, 16),
    ("Curd Rice", "veg", 190, 8), ("Lemon Rice", "vegan", 185, 10),
]

DESSERTS = [
    ("Gulab Jamun", "veg", 140, "Two warm milk dumplings in cardamom syrup."),
    ("Rasmalai", "veg", 170, "Cottage cheese discs in saffron-thickened milk."),
    ("Gajar Halwa", "veg", 180, "Carrots slow-cooked in milk, ghee and cardamom."),
    ("Kheer", "veg", 150, "Rice pudding simmered with almonds and raisins."),
    ("Jalebi", "veg", 130, "Coils of fermented batter, fried and syrup-soaked."),
    ("Kulfi Falooda", "veg", 200, "Dense frozen kulfi over rose vermicelli."),
    ("Vegan Mango Sorbet", "vegan", 180, "Alphonso mango churned with lime, no dairy."),
    ("Vegan Coconut Ladoo", "vegan", 160, "Toasted coconut rolled with jaggery."),
    ("Chocolate Brownie", "veg", 190, "Warm brownie with a molten centre."),
    ("Shahi Tukda", "veg", 175, "Fried bread soaked in saffron rabri."),
    ("Phirni", "veg", 155, "Ground rice set in earthenware with pistachio."),
    ("Malpua", "veg", 165, "Griddled pancakes steeped in cardamom syrup."),
]

BEVERAGES = [
    ("Masala Chai", "veg", 60), ("Adrak Chai", "veg", 60), ("Kashmiri Kahwa", "vegan", 90),
    ("Filter Coffee", "veg", 80), ("Cold Coffee", "veg", 140), ("Sweet Lassi", "veg", 120),
    ("Salted Lassi", "veg", 110), ("Mango Lassi", "veg", 150), ("Rose Lassi", "veg", 140),
    ("Fresh Lime Soda", "vegan", 90), ("Jaljeera", "vegan", 80), ("Aam Panna", "vegan", 100),
    ("Buttermilk", "veg", 70), ("Sugarcane Juice", "vegan", 95),
    ("Tender Coconut Water", "vegan", 110), ("Badam Milk", "veg", 130),
    ("Thandai", "veg", 145), ("Nimbu Pani", "vegan", 70),
    ("Mineral Water", "vegan", 40), ("Cola", "vegan", 60),
]

COUPON_ROWS = [
    ("SAVE20", "percent", 20, 800, 300, True, "20% off orders above 800"),
    ("FIRST50", "flat", 50, 300, 50, True, "Flat 50 off your first order"),
    ("FREEDEL", "free_delivery", 0, 500, 0, True, "Free delivery on orders above 500"),
    ("CHEF10", "percent", 10, 0, 150, True, "10% off, no minimum"),
    ("BIGBITE15", "percent", 15, 1200, 400, True, "15% off orders above 1200"),
    ("LUNCH75", "flat", 75, 600, 75, True, "Flat 75 off lunch orders"),
    ("FAMILY25", "percent", 25, 2000, 600, True, "25% off family-sized orders"),
    ("WELCOME100", "flat", 100, 900, 100, True, "Flat 100 off for new customers"),
    ("MONSOON25", "percent", 25, 1500, 500, False, "Seasonal offer (expired)"),
    ("DIWALI30", "percent", 30, 1800, 700, False, "Festive offer (expired)"),
    ("SUMMER40", "flat", 40, 400, 40, False, "Summer offer (expired)"),
    ("NEWYEAR20", "percent", 20, 1000, 350, False, "New Year offer (expired)"),
    ("STUDENT10", "percent", 10, 350, 120, True, "10% student discount"),
    ("LATENIGHT60", "flat", 60, 700, 60, True, "Flat 60 off after 10pm"),
]

ZONE_NAMES = [
    "City Centre", "Riverside", "Outer Ring", "Old Town", "Tech Park", "Lake View",
    "Market Square", "Hill Road", "Station Road", "Green Valley", "Fort Area",
    "Airport Road",
]

FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Diya", "Ananya", "Ishaan", "Kabir",
               "Meera", "Rohan", "Saanvi", "Arjun", "Priya", "Zara", "Kiran",
               "Neha", "Rahul", "Sana", "Yash", "Tara", "Imran", "Waqar", "Fatima",
               "Bilal", "Ayesha", "Hassan", "Nida", "Omar", "Rida", "Sami", "Zoya"]
LAST_NAMES = ["Sharma", "Verma", "Patel", "Reddy", "Nair", "Iyer", "Khan", "Ahmed",
              "Singh", "Kapoor", "Bose", "Das", "Gupta", "Malik", "Chowdhury",
              "Rao", "Menon", "Pillai", "Joshi", "Shah"]
STREETS = ["Nehru Street", "MG Road", "Gandhi Lane", "Park Avenue", "Church Street",
           "Lake Road", "Station Road", "Mill Lane", "Rose Garden", "Palm Grove"]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_dishes() -> list[dict]:
    """Compose the whole catalogue. Deterministic given RNG_SEED."""
    rng = random.Random(RNG_SEED)
    dishes: list[dict] = []

    def add(name, category, diet, price, spice, prep, description, tags):
        dishes.append({
            "code": f"D{len(dishes) + 1:04d}", "name": name, "category": category,
            "diet": diet, "price": Decimal(str(round(price, 2))), "spice": spice,
            "prep_minutes": prep, "description": description, "tags": " ".join(tags),
            "is_available": True, "times_ordered": 0,
        })

    for protein, diet, factor in STARTER_PROTEINS:
        for style, spice, prep, base, blurb in STARTER_STYLES:
            price = round(base * factor / 10) * 10
            add(f"{protein} {style}", "Starters", diet, price, spice, prep,
                f"{protein}. {blurb}", ["starter", style.lower(), spice, diet])

    for protein, diet, factor in MAIN_PROTEINS:
        for gravy, spice, prep, base, blurb in MAIN_GRAVIES:
            price = round(base * factor / 10) * 10
            add(f"{protein} {gravy}", "Mains", diet, price, spice, prep,
                f"{protein}. {blurb}", ["main", "curry", spice, diet])

    for name, diet, price, spice, prep, blurb in CLASSIC_MAINS:
        add(name, "Mains", diet, price, spice, prep, blurb,
            ["main", "classic", spice, diet])

    for name, price, prep in BREADS:
        diet = "nonveg" if "Keema" in name else "veg"
        add(name, "Breads", diet, price, "mild", prep,
            "Fresh from the tandoor, brushed and served hot.",
            ["bread", "tandoor", diet])

    for style, factor in BIRYANI_STYLES:
        for protein, diet, base in BIRYANI_PROTEINS:
            price = round(base * factor / 10) * 10
            add(f"{style} {protein} Biryani", "Rice & Biryani", diet, price,
                "hot" if style == "Hyderabadi" else "medium", 32,
                f"Dum-cooked basmati layered with {protein.lower()} in the "
                f"{style} style.", ["biryani", "rice", style.lower(), diet])

    for name, diet, price, prep in RICE_SIDES:
        add(name, "Rice & Biryani", diet, price, "mild", prep,
            "A simple rice side that carries a gravy well.", ["rice", "side", diet])

    for name, diet, price, blurb in DESSERTS:
        add(name, "Desserts", diet, price, "mild", rng.randint(4, 10), blurb,
            ["dessert", "sweet", diet])

    for name, diet, price in BEVERAGES:
        add(name, "Beverages", diet, price, "mild", rng.randint(3, 8),
            "Served chilled or hot, as it should be.", ["beverage", "drink", diet])

    return dishes


def _build_zones() -> list[dict]:
    """60 delivery zones, keyed by pincode prefix 700-759."""
    rng = random.Random(RNG_SEED + 1)
    zones = []
    for index, prefix in enumerate(range(700, 760)):
        name = ZONE_NAMES[index % len(ZONE_NAMES)]
        tier = index % 3
        zones.append({
            "pincode_prefix": str(prefix),
            "zone_name": f"Zone {chr(65 + tier)} - {name}",
            "fee": Decimal(str([29.0, 49.0, 89.0][tier])),
            "eta_minutes": [30, 45, 65][tier] + rng.randint(-3, 5),
        })
    return zones


def _build_users() -> list[dict]:
    """200 synthetic customers plus one documented demo account."""
    rng = random.Random(RNG_SEED + 2)
    # One shared hash for the synthetic users: PBKDF2 is deliberately slow, and
    # hashing 200 throwaway passwords would add half a minute to every seed.
    filler_hash = hash_password("customer12345")

    users = [{
        "email": DEMO_EMAIL, "display_name": "Demo Customer",
        "password_hash": hash_password(DEMO_PASSWORD),
        "created_at": utcnow() - timedelta(days=200), "last_seen_at": utcnow(),
    }]

    for index in range(200):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        users.append({
            "email": f"{first.lower()}.{last.lower()}{index}@example.com",
            "display_name": f"{first} {last}",
            "password_hash": filler_hash,
            "created_at": utcnow() - timedelta(days=rng.randint(30, 400)),
            "last_seen_at": utcnow() - timedelta(days=rng.randint(0, 30)),
        })
    return users


def _build_orders(dishes: list[Dish], users: list[User], zones: list[dict],
                  count: int) -> tuple[list[dict], list[dict], dict[int, int]]:
    """Generate orders and their lines. Returns (orders, items, popularity)."""
    rng = random.Random(RNG_SEED + 3)

    # A long-tail popularity curve, so "best sellers" actually means something
    # instead of every dish selling roughly the same amount.
    weights = [1 / (rank + 3) ** 0.75 for rank in range(len(dishes))]
    shuffled = dishes[:]
    rng.shuffle(shuffled)

    demo_user = users[0]
    # Weight the demo account heavily so its history is worth demonstrating.
    user_pool = [demo_user] * 40 + users[1:]

    orders: list[dict] = []
    items: list[dict] = []
    popularity: dict[int, int] = {}
    now = utcnow()

    for index in range(count):
        user = rng.choice(user_pool)
        placed_at = now - timedelta(minutes=rng.randint(60, 180 * 24 * 60))

        chosen = rng.choices(shuffled, weights=weights, k=rng.randint(1, 5))
        lines: dict[int, int] = {}
        for dish in chosen:
            lines[dish.id] = lines.get(dish.id, 0) + rng.randint(1, 3)

        by_id = {d.id: d for d in dishes}
        subtotal = sum(float(by_id[i].price) * q for i, q in lines.items())

        discount = 0.0
        coupon_code = None
        if rng.random() < 0.35 and subtotal >= 800:
            coupon_code = "SAVE20"
            discount = round(min(subtotal * 0.20, 300.0), 2)

        zone = rng.choice(zones)
        delivery_fee = float(zone["fee"])
        taxable = max(subtotal - discount, 0.0)
        tax = round(taxable * TAX_RATE, 2)
        total = round(taxable + delivery_fee + PACKAGING_FEE + tax, 2)

        cancelled_at = placed_at + timedelta(minutes=4) if rng.random() < 0.03 else None
        order_id = index + 1

        orders.append({
            # Same formula the app uses for new orders, so seeded codes and
            # live ones can never collide.
            "id": order_id, "code": order_code_for(order_id), "user_id": user.id,
            "placed_at": placed_at,
            "eta_minutes": zone["eta_minutes"] + rng.randint(10, 30),
            "cancelled_at": cancelled_at,
            "cancel_reason": "Changed my mind" if cancelled_at else None,
            "customer_name": user.display_name,
            "phone": f"9{rng.randint(100000000, 999999999)}",
            "address": f"{rng.randint(1, 240)} {rng.choice(STREETS)}",
            "pincode": f"{zone['pincode_prefix']}{rng.randint(100, 999)}",
            "payment_method": rng.choice(PAYMENT_METHODS),
            "subtotal": Decimal(str(round(subtotal, 2))),
            "discount": Decimal(str(discount)),
            "delivery_fee": Decimal(str(delivery_fee)),
            "packaging_fee": Decimal(str(PACKAGING_FEE)),
            "tax": Decimal(str(tax)), "total": Decimal(str(total)),
            "coupon_code": coupon_code,
        })

        for dish_id, quantity in lines.items():
            dish = by_id[dish_id]
            items.append({"order_id": order_id, "dish_id": dish_id,
                          "dish_name": dish.name, "unit_price": dish.price,
                          "quantity": quantity})
            if cancelled_at is None:
                popularity[dish_id] = popularity.get(dish_id, 0) + quantity

    return orders, items, popularity


def _seed_demo_conversation(db, demo_user: User) -> None:
    """One worked example in the demo account's chat history.

    Seeded so a brand-new checkout has something in the sidebar; every other
    conversation in the database is real traffic from the running app.
    """
    conversation = Conversation(
        user_id=demo_user.id, title="Dinner for two last Friday",
        created_at=utcnow() - timedelta(days=3),
        updated_at=utcnow() - timedelta(days=3))
    db.add(conversation)
    db.flush()

    transcript = [
        ("user", "what do you recommend for two people?"),
        ("assistant", "Our best sellers right now are the Hyderabadi Chicken "
                      "Biryani and Paneer Butter Masala. Add a Garlic Naan and "
                      "that comfortably feeds two."),
        ("user", "sounds good, what would that come to?"),
        ("assistant", "That comes to Rs.1,043 delivered to Zone A, including "
                      "packaging and 5% tax. Shall I place it?"),
    ]
    for seq, (role, content) in enumerate(transcript, start=1):
        db.add(ChatMessage(conversation_id=conversation.id, seq=seq, role=role,
                           content=content,
                           created_at=utcnow() - timedelta(days=3)))


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def ensure_database_exists() -> None:
    """CREATE DATABASE if the server does not have it yet (SQL Server only).

    SQLAlchemy can create tables but not the database that holds them, so this
    connects to `master` first. SQLite and a pre-created Postgres database skip
    it entirely.
    """
    url = make_url(engine.url)
    if not url.drivername.startswith("mssql") or not url.database:
        return

    from sqlalchemy import create_engine as _create_engine

    admin = _create_engine(url.set(database="master"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM sys.databases WHERE name = :name"),
            {"name": url.database}).scalar()
        if not exists:
            print(f"  creating database [{url.database}] ...")
            # The name cannot be a bind parameter in DDL, so it is quoted
            # instead. It comes from our own config, never from user input.
            connection.execute(text(f"CREATE DATABASE [{url.database}]"))
    admin.dispose()


def seed(order_count: int = 12_000, force: bool = False) -> dict[str, int]:
    """Create the schema and fill it. Returns the row counts written."""
    ensure_database_exists()
    Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        if not force and db.scalar(select(func.count(Dish.id))):
            return {}  # already seeded

        print("  building menu ...")
        db.bulk_insert_mappings(Dish, _build_dishes())
        db.bulk_insert_mappings(Coupon, [
            {"code": code, "kind": kind, "value": Decimal(str(value)),
             "min_order": Decimal(str(min_order)),
             "max_discount": Decimal(str(max_discount)),
             "is_active": active, "label": label}
            for code, kind, value, min_order, max_discount, active, label in COUPON_ROWS
        ])
        zones = _build_zones()
        db.bulk_insert_mappings(DeliveryZone, zones)
        db.bulk_insert_mappings(User, _build_users())
        db.commit()

        dishes = list(db.scalars(select(Dish)))
        users = list(db.scalars(select(User).order_by(User.id)))

        print(f"  generating {order_count:,} orders ...")
        orders, items, popularity = _build_orders(dishes, users, zones, order_count)

        # Explicit ids let the 36k order_items reference their orders without a
        # round-trip per row; SQL Server needs IDENTITY_INSERT for that.
        _bulk_insert_with_ids(db, Order, orders, "orders")
        db.bulk_insert_mappings(OrderItem, items)

        for dish in dishes:
            dish.times_ordered = popularity.get(dish.id, 0)

        _seed_demo_conversation(db, users[0])
        db.commit()

        return {"dishes": len(dishes), "coupons": len(COUPON_ROWS),
                "zones": len(zones), "users": len(users),
                "orders": len(orders), "order_items": len(items)}
    finally:
        db.close()


def _bulk_insert_with_ids(db, model, rows: list[dict], table: str) -> None:
    """Bulk insert rows that carry explicit primary keys."""
    is_mssql = engine.url.drivername.startswith("mssql")
    if is_mssql:
        db.execute(text(f"SET IDENTITY_INSERT {table} ON"))
    db.bulk_insert_mappings(model, rows)
    if is_mssql:
        db.execute(text(f"SET IDENTITY_INSERT {table} OFF"))


def summary() -> dict[str, int]:
    """Row counts for every table, for the startup banner."""
    from app.models import CartItem, ChatMessage as CM, Conversation as Conv, ToolInvocation

    db = SessionLocal()
    try:
        return {
            name: int(db.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in [
                ("dishes", Dish), ("coupons", Coupon), ("zones", DeliveryZone),
                ("users", User), ("orders", Order), ("order_items", OrderItem),
                ("conversations", Conv), ("chat_messages", CM),
                ("cart_items", CartItem), ("tool_invocations", ToolInvocation),
            ]
        }
    finally:
        db.close()
