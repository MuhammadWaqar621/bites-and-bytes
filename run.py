"""Entry point: `python run.py` starts the web UI.

Prints a configuration summary first, because "nothing happens when I chat" is
almost always a missing or misspelled variable in `.env`.
"""

from __future__ import annotations

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()

    print("\n  Bites & Bytes - Azure OpenAI tool-calling demo")
    print("  " + "-" * 46)
    print(f"  endpoint    : {settings.endpoint or '(not set)'}")
    print(f"  deployment  : {settings.deployment or '(not set)'}")
    print(f"  api version : {settings.api_version}")
    if settings.is_configured:
        print("  status      : ready")
    else:
        print("  status      : NOT configured - missing " + ", ".join(settings.missing))
        print("                the UI will load but chatting will return a 503")
    print(f"\n  open http://127.0.0.1:{settings.port}\n")

    uvicorn.run("app.server:app", host="127.0.0.1", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
