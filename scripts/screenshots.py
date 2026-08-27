"""Capture the README screenshots from the running app.

    python run.py                     # in one terminal
    python scripts/screenshots.py     # in another

Drives a real Chromium against a real server and a real database, so the images
in the README are the app rather than a mockup. Every shot is regenerated from
scratch on each run: a fresh account is registered, a scripted conversation is
held with the live model, and the results are photographed.

Requires `pip install playwright && playwright install chromium` -- both are
dev-only, which is why they are not in requirements.txt.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
VIEWPORT = {"width": 1600, "height": 1000}

#: A throwaway account per run, so the "new customer" shots really are new.
ACCOUNT = {
    "email": f"portfolio{int(time.time())}@bitesbytes.app",
    "password": "portfolio123",
    "display_name": "Waqar",
    "phone": "9876543210",
    "address": "Flat 4B, Nehru Street",
    "pincode": "755001",
}

DEMO = {"email": "demo@bitesbytes.app", "password": "demo12345"}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)

        try:
            page.goto(BASE, wait_until="networkidle")
        except Exception:
            sys.exit(f"Could not reach {BASE}. Start the app with `python run.py` first.")

        shoot_login(page)
        shoot_signup(page)
        shoot_first_order(page)
        shoot_demo_account(page)

        browser.close()

    print(f"\n  {len(list(OUT.glob('*.png')))} screenshots written to {OUT}")


# ---------------------------------------------------------------------------
# Shots
# ---------------------------------------------------------------------------

def shoot_login(page: Page) -> None:
    """1. The login gate — the only thing a visitor sees before signing in."""
    page.wait_for_selector("#auth-screen:not([hidden])")
    save(page, "01-login")


def shoot_signup(page: Page) -> None:
    """2. Create account — email and password required, the rest optional."""
    page.click('.auth-tab[data-mode="register"]')
    page.wait_for_selector("#optional-fields:not([hidden])")
    fill(page, ACCOUNT)
    save(page, "02-create-account")


def shoot_first_order(page: Page) -> None:
    """3-6. A brand-new account: recommendations, then an order in one turn."""
    submit_auth(page)

    send(page, "What do you recommend?")
    save(page, "03-chat-recommendations")

    send(page, "Add 2 garlic naan and a dal makhani, then place the order "
               "using my saved details, paying cash on delivery")
    # The assistant reads back the saved details and asks before committing, so
    # confirm -- otherwise this shot shows a question, not a placed order.
    send(page, "Yes, go ahead")
    save(page, "04-order-placed")

    # The trace panel is the point of the whole project: expand the first two
    # calls so the arguments and results are actually visible in the image.
    page.click('.tab[data-tab="trace"]')
    for item in page.query_selector_all(".trace-item")[:2]:
        item.query_selector(".trace-head").click()
    page.wait_for_timeout(250)
    save(page, "05-tool-trace")

    page.click('.tab[data-tab="tools"]')
    page.wait_for_timeout(250)
    save(page, "06-tool-catalogue")


def shoot_demo_account(page: Page) -> None:
    """7-9. The seeded account, which has ~2,000 orders to analyse."""
    page.click("#logout-btn")
    page.wait_for_selector("#auth-screen:not([hidden])")

    # The form is still in create-account mode from the previous shot, and it
    # stays that way across a sign-out -- switch back or this tries to register
    # the demo address a second time.
    page.click('.auth-tab[data-mode="login"]')
    # state="hidden", not the default "visible": a hidden element is never
    # visible, so the default would wait out the whole timeout.
    page.wait_for_selector("#optional-fields", state="hidden")
    fill(page, DEMO)
    submit_auth(page)

    # Tab selection lives in the DOM and survives a sign-out, so the left panel
    # is still showing Tools from the previous shot.
    page.click('.tab[data-tab="chats"]')
    page.click("#new-chat-btn")
    page.wait_for_timeout(400)

    send(page, "How much did I spend this month?")
    save(page, "07-spend-summary")

    send(page, "Show my orders per month for the last 6 months")
    save(page, "08-trend-charts")

    send(page, "What do I spend the most on?")
    save(page, "09-breakdown-chart")

    page.click('.tab[data-tab="menu"]')
    page.wait_for_timeout(250)
    save(page, "10-menu-panel")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def submit_auth(page: Page) -> None:
    """Submit the auth form and wait for whichever outcome arrives.

    Waiting only for the app to appear turns any rejected password into an
    opaque 30-second timeout; racing it against the error banner reports what
    actually went wrong.
    """
    page.click("#auth-submit")
    page.wait_for_selector("#app:not([hidden]), #auth-error:not([hidden])",
                           timeout=60_000)
    banner = page.query_selector("#auth-error:not([hidden])")
    if banner:
        sys.exit(f"Auth failed: {banner.inner_text()}")
    page.wait_for_selector("#app:not([hidden])", timeout=60_000)


def fill(page: Page, values: dict[str, str]) -> None:
    for name, value in values.items():
        page.fill(f'#auth-form [name="{name}"]', value)


def send(page: Page, message: str) -> None:
    """Type a message and wait for the assistant to finish answering.

    Waits on the composer being re-enabled rather than a fixed sleep: a turn
    that runs three tools takes several seconds longer than one that runs none.
    """
    page.fill("#message-input", message)
    page.click("#send-btn")
    page.wait_for_selector("#message-input:not([disabled])", timeout=120_000)
    page.wait_for_timeout(600)  # let the rise animation settle


def save(page: Page, name: str) -> None:
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"  captured {path.name}")


if __name__ == "__main__":
    main()
