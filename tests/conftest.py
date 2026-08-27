"""Test fixtures.

The whole suite runs against a throwaway **SQLite** file, not SQL Server. That
is the payoff of routing every query through `repository.py`: the same tool
code under test is the code that runs in production, but the tests need no
database server, no credentials and no network.

`DATABASE_URL` is set before any `app.*` import, because `app.db` builds the
engine at import time from whatever the environment says then.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_DB = Path(tempfile.gettempdir()) / "bitesbytes_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"

import pytest  # noqa: E402 - must follow the env var above

from app import models  # noqa: E402,F401 - registers the tables on Base
from app import repository as repo  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Coupon, DeliveryZone, Dish  # noqa: E402
from app.tools import ToolContext  # noqa: E402

#: A deliberately small catalogue with prices the assertions can hard-code.
DISHES = [
    ("Paneer Tikka", "Starters", "veg", 320, "medium", 18),
    ("Crispy Corn", "Starters", "vegan", 240, "mild", 12),
    ("Chicken 65", "Starters", "nonveg", 340, "hot", 20),
    ("Butter Chicken", "Mains", "nonveg", 480, "mild", 25),
    ("Paneer Butter Masala", "Mains", "veg", 420, "mild", 22),
    ("Chana Masala", "Mains", "vegan", 280, "medium", 18),
    ("Rogan Josh", "Mains", "nonveg", 520, "hot", 35),
    ("Garlic Naan", "Breads", "veg", 80, "mild", 8),
    ("Veg Dum Biryani", "Rice & Biryani", "veg", 380, "medium", 30),
    ("Hyderabadi Chicken Biryani", "Rice & Biryani", "nonveg", 460, "hot", 32),
    ("Masala Chai", "Beverages", "veg", 60, "mild", 6),
]

COUPONS = [
    ("SAVE20", "percent", 20, 800, 300, True, "20% off orders above 800"),
    ("CHEF10", "percent", 10, 0, 150, True, "10% off, no minimum"),
    ("FREEDEL", "free_delivery", 0, 500, 0, True, "Free delivery above 500"),
    ("MONSOON25", "percent", 25, 1500, 500, False, "Seasonal offer (expired)"),
]

ZONES = [("755", "Zone A - City Centre", 29, 30), ("757", "Zone C - Outer Ring", 89, 65)]


@pytest.fixture()
def db():
    """A clean database per test. SQLite makes drop/create effectively free."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    session = SessionLocal()
    for name, category, diet, price, spice, prep in DISHES:
        session.add(Dish(
            code=f"D{len(name):04d}{price}", name=name, category=category, diet=diet,
            price=price, spice=spice, prep_minutes=prep,
            description=f"{name} description.", tags=f"{category.lower()} {diet}",
            is_available=True, times_ordered=0))
    for code, kind, value, min_order, max_discount, active, label in COUPONS:
        session.add(Coupon(code=code, kind=kind, value=value, min_order=min_order,
                           max_discount=max_discount, is_active=active, label=label))
    for prefix, zone_name, fee, eta in ZONES:
        session.add(DeliveryZone(pincode_prefix=prefix, zone_name=zone_name,
                                 fee=fee, eta_minutes=eta))
    session.commit()

    yield session
    session.close()


@pytest.fixture()
def user(db):
    person = repo.create_user(db, "waqar@example.com", "Waqar", "supersecret1")
    db.commit()
    return person


@pytest.fixture()
def ctx(db, user) -> ToolContext:
    """The context every tool receives: database, signed-in user, open chat."""
    conversation = repo.create_conversation(db, user, "Test chat")
    db.commit()
    return ToolContext(db=db, user=user, conversation=conversation)


@pytest.fixture()
def other_ctx(db) -> ToolContext:
    """A second, unrelated customer -- used to prove data cannot leak across."""
    stranger = repo.create_user(db, "stranger@example.com", "Stranger", "supersecret2")
    conversation = repo.create_conversation(db, stranger, "Stranger chat")
    db.commit()
    return ToolContext(db=db, user=stranger, conversation=conversation)
