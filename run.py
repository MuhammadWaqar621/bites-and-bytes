"""Entry point: `python run.py` does everything.

It creates the database if it is missing, builds the schema, seeds ~48,000 rows
the first time, prints what it found, and starts the web server. Running it a
second time skips straight to the server -- the seeder is idempotent.
"""

from __future__ import annotations

import sys

import uvicorn

from app.config import get_settings
from app.seed import DEMO_EMAIL, DEMO_PASSWORD, seed, summary


def main() -> None:
    settings = get_settings()

    print("\n  Bites & Bytes - Azure OpenAI tool-calling demo")
    print("  " + "-" * 52)
    print(f"  endpoint    : {settings.endpoint or '(not set)'}")
    print(f"  deployment  : {settings.deployment or '(not set)'}")
    print(f"  database    : {_safe_url(settings.database_url)}")

    try:
        written = seed()
    except Exception as exc:  # noqa: BLE001 - the message matters, not the type
        print(f"\n  DATABASE ERROR: {exc}\n")
        print("  Check that SQL Server is running and that DATABASE_URL in .env")
        print("  points at it. To use SQLite instead, set:")
        print("    DATABASE_URL=sqlite:///bitesbytes.db\n")
        sys.exit(1)

    if written:
        print("  seeded      : " + ", ".join(f"{v:,} {k}" for k, v in written.items()))
    else:
        counts = summary()
        print("  rows        : " + ", ".join(f"{v:,} {k}" for k, v in counts.items()
                                             if v))

    if settings.is_configured:
        print("  llm         : ready")
    else:
        print("  llm         : NOT configured - missing " + ", ".join(settings.missing))
        print("                the UI will load but chatting will return a 503")

    print(f"\n  demo login  : {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"  open        : http://127.0.0.1:{settings.port}\n")

    uvicorn.run("app.server:app", host="127.0.0.1", port=settings.port, reload=False)


def _safe_url(url: str) -> str:
    """Never print a password, even from a local connection string."""
    if "@" in url and "//" in url:
        scheme, rest = url.split("//", 1)
        return f"{scheme}//***@{rest.split('@', 1)[1]}"
    return url


if __name__ == "__main__":
    main()
