"""FastAPI layer: serves the web UI and exposes the agent over HTTP.

Deliberately thin. It owns no business logic -- it creates the backend, the
session store and the agent once at import time, then translates HTTP into
method calls. Everything interesting lives in :mod:`app.agent` and
:mod:`app.tools`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.agent import OrderingAgent
from app.config import PROJECT_ROOT, get_settings
from app.data import CATEGORIES, COUPONS, CURRENCY, MENU
from app.llm_client import AzureChatBackend, LLMError
from app.schemas import TOOL_SCHEMAS, tool_catalogue
from app.store import SessionStore

WEB_DIR = Path(PROJECT_ROOT) / "web"

settings = get_settings()
store = SessionStore()

# The backend is only built when credentials exist, so the menu and the UI stay
# browsable on a machine with no .env -- only /api/chat needs the model.
_backend = AzureChatBackend(settings) if settings.is_configured else None
agent = (
    OrderingAgent(_backend, store, max_iterations=settings.max_tool_iterations)
    if _backend
    else None
)

app = FastAPI(
    title="Bites & Bytes - Tool Calling Demo",
    description="A restaurant ordering agent driven by 12 Azure OpenAI tools.",
    version=__version__,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class SessionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    """Told the UI whether chatting will work, and why not if it will not."""
    return {
        "version": __version__,
        "configured": settings.is_configured,
        "missing_env": settings.missing,
        "deployment": settings.deployment or None,
        "api_version": settings.api_version,
        "tool_count": len(TOOL_SCHEMAS),
    }


@app.get("/api/menu")
def menu() -> dict:
    """The full menu, grouped for the sidebar."""
    return {
        "currency": CURRENCY,
        "categories": CATEGORIES,
        "dishes": MENU,
        "coupons": [
            {"code": code, "label": data["label"], "active": data["active"],
             "min_order": data["min_order"]}
            for code, data in COUPONS.items()
        ],
    }


@app.get("/api/tools")
def tools() -> dict:
    """The catalogue exactly as the model receives it, for the Tools panel."""
    return {"count": len(TOOL_SCHEMAS), "tools": tool_catalogue()}


@app.get("/api/cart")
def cart(session_id: str) -> dict:
    """Current cart for a session, so the UI can render it outside the chat."""
    return _cart_view(session_id)


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    """One customer message in, one assistant answer plus tool trace out."""
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI is not configured. Missing: "
                   + ", ".join(settings.missing)
                   + ". Add them to .env and restart the server.",
        )

    session = store.get(request.session_id)
    try:
        reply = agent.respond(session, request.message)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    payload = reply.as_dict()
    payload.update(_cart_view(request.session_id))
    return payload


@app.post("/api/reset")
def reset(request: SessionRequest) -> dict:
    """Wipe the cart, coupon, orders and chat history for one session."""
    store.reset(request.session_id)
    return {"ok": True, **_cart_view(request.session_id)}


# ---------------------------------------------------------------------------
# Static UI (mounted last so it never shadows the /api routes)
# ---------------------------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def _cart_view(session_id: str) -> dict:
    """Cart + orders shaped for the right-hand panel."""
    session = store.get(session_id)
    return {
        "cart": {
            "currency": CURRENCY,
            "items": session.cart_as_list(),
            "subtotal": session.subtotal(),
            "coupon_code": session.coupon_code,
            "item_count": sum(line.quantity for line in session.cart.values()),
        },
        "orders": [
            {"order_id": order.order_id, "status": order.status(),
             "total": order.totals.get("total"), "eta_minutes": order.eta_minutes}
            for order in session.orders.values()
        ],
    }
