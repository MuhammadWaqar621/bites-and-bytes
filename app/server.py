"""FastAPI layer: accounts, conversations, and the chat endpoint.

Deliberately thin. It owns no business logic -- it authenticates the caller,
loads the conversation they asked for (checking they own it), hands both to the
agent, and shapes the result for the browser.

Every private route depends on :func:`app.auth.current_user`, and every
conversation is fetched with a ``user_id`` predicate. Ownership is enforced in
SQL, not in the prompt.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session as DbSession

from app import __version__
from app import repository as repo
from app.agent import OrderingAgent
from app.auth import COOKIE_NAME, clear_session_cookie, current_user, set_session_cookie
from app.config import PROJECT_ROOT, get_settings
from app.data import CATEGORIES, CURRENCY
from app.db import get_db
from app.llm_client import AzureChatBackend, LLMError
from app.models import User
from app.schemas import TOOL_SCHEMAS, tool_catalogue
from app.security import validate_password
from app.tools import ToolContext

WEB_DIR = Path(PROJECT_ROOT) / "web"

settings = get_settings()

# The backend is only built when credentials exist, so the UI and the menu stay
# browsable on a machine with no .env -- only /api/chat needs the model.
_backend = AzureChatBackend(settings) if settings.is_configured else None
agent = OrderingAgent(_backend, settings.max_tool_iterations) if _backend else None

app = FastAPI(
    title="Bites & Bytes - Tool Calling Demo",
    description="A multi-user restaurant ordering agent driven by 15 Azure "
                "OpenAI tools over SQL Server.",
    version=__version__,
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """Sign-up. Email and password are the only required fields.

    The rest are conveniences: supplying them lets `get_my_profile` offer
    delivery details on the very first order instead of asking for five things
    in the chat. Leaving them blank costs nothing.
    """

    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=250)
    pincode: str | None = Field(default=None, max_length=10)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    conversation_id: int
    message: str = Field(min_length=1, max_length=2000)


class NewChatRequest(BaseModel):
    title: str = Field(default="New chat", max_length=120)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@app.post("/api/auth/register")
def register(request: RegisterRequest, response: Response,
             db: DbSession = Depends(get_db)) -> dict:
    if repo.get_user_by_email(db, request.email):
        raise HTTPException(409, "An account with that email already exists.")

    problem = validate_password(request.password)
    if problem:
        raise HTTPException(422, problem)

    user = repo.create_user(
        db, request.email, request.password,
        display_name=request.display_name, phone=request.phone,
        address=request.address, pincode=request.pincode)
    token = repo.issue_token(db, user, settings.session_days)
    db.commit()

    set_session_cookie(response, token)
    return {"user": _user_view(user)}


@app.post("/api/auth/login")
def login(request: LoginRequest, response: Response,
          db: DbSession = Depends(get_db)) -> dict:
    user = repo.authenticate(db, request.email, request.password)
    if user is None:
        # One message for both cases, so this cannot be used to discover which
        # email addresses have accounts.
        raise HTTPException(401, "Email or password is incorrect.")

    token = repo.issue_token(db, user, settings.session_days)
    db.commit()

    set_session_cookie(response, token)
    return {"user": _user_view(user)}


@app.post("/api/auth/logout")
def logout(response: Response, bb_session: str | None = Cookie(default=None),
           db: DbSession = Depends(get_db)) -> dict:
    if bb_session:
        repo.revoke_token(db, bb_session)
        db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user), db: DbSession = Depends(get_db)) -> dict:
    return {"user": _user_view(user), **repo.order_stats(db, user)}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@app.get("/api/conversations")
def conversations(user: User = Depends(current_user),
                  db: DbSession = Depends(get_db)) -> dict:
    return {"conversations": repo.list_conversations(db, user)}


@app.post("/api/conversations")
def new_conversation(request: NewChatRequest, user: User = Depends(current_user),
                     db: DbSession = Depends(get_db)) -> dict:
    conversation = repo.create_conversation(db, user, request.title)
    db.commit()
    return {"id": conversation.id, "title": conversation.title}


@app.get("/api/conversations/{conversation_id}")
def conversation_detail(conversation_id: int, user: User = Depends(current_user),
                        db: DbSession = Depends(get_db)) -> dict:
    conversation = _own_conversation(db, user, conversation_id)
    messages = repo.load_messages(db, conversation.id)
    return {
        "id": conversation.id,
        "title": conversation.title,
        "messages": repo.visible_transcript(messages),
        "trace": repo.list_invocations(db, conversation.id),
        **_cart_view(db, user, conversation),
    }


@app.delete("/api/conversations/{conversation_id}")
def remove_conversation(conversation_id: int, user: User = Depends(current_user),
                        db: DbSession = Depends(get_db)) -> dict:
    if not repo.delete_conversation(db, user, conversation_id):
        raise HTTPException(404, "Chat not found.")
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@app.post("/api/chat")
def chat(request: ChatRequest, user: User = Depends(current_user),
         db: DbSession = Depends(get_db)) -> dict:
    """One customer message in, one assistant answer plus tool trace out."""
    if agent is None:
        raise HTTPException(
            503,
            "Azure OpenAI is not configured. Missing: " + ", ".join(settings.missing)
            + ". Add them to .env and restart the server.",
        )

    conversation = _own_conversation(db, user, request.conversation_id)

    # Name the chat after its opening line, the way every chat app does.
    if conversation.title == "New chat":
        conversation.title = request.message[:60]

    try:
        reply = agent.respond(ToolContext(db, user, conversation), request.message)
    except LLMError as exc:
        db.rollback()
        raise HTTPException(502, str(exc)) from exc

    db.commit()

    payload = reply.as_dict()
    payload["title"] = conversation.title
    payload.update(_cart_view(db, user, conversation))
    return payload


# ---------------------------------------------------------------------------
# Public reference data
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health(db: DbSession = Depends(get_db)) -> dict:
    """Tells the UI whether chatting will work, and why not if it will not."""
    return {
        "version": __version__,
        "configured": settings.is_configured,
        "missing_env": settings.missing,
        "deployment": settings.deployment or None,
        "tool_count": len(TOOL_SCHEMAS),
        "dish_count": repo.count_dishes(db),
    }


@app.get("/api/menu")
def menu(db: DbSession = Depends(get_db)) -> dict:
    """The menu for the sidebar. Public: you can read it before signing in."""
    dishes = repo.list_dishes(db, limit=400)
    return {
        "currency": CURRENCY,
        "categories": CATEGORIES,
        "total": repo.count_dishes(db),
        "dishes": [
            {**repo.dish_public(dish), "prep_minutes": dish.prep_minutes,
             "times_ordered": dish.times_ordered}
            for dish in dishes
        ],
        "coupons": [
            {"code": c.code, "label": c.label, "active": c.is_active,
             "min_order": repo.money(c.min_order)}
            for c in repo.list_coupons(db)
        ],
    }


@app.get("/api/tools")
def tools() -> dict:
    """The catalogue exactly as the model receives it, for the Tools panel."""
    return {"count": len(TOOL_SCHEMAS), "tools": tool_catalogue()}


# ---------------------------------------------------------------------------
# Static UI (mounted last so it never shadows the /api routes)
# ---------------------------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _own_conversation(db: DbSession, user: User, conversation_id: int):
    """Load a conversation or 404. The ownership check is inside the query."""
    conversation = repo.get_conversation(db, user, conversation_id)
    if conversation is None:
        # 404 rather than 403: never confirm that somebody else's chat exists.
        raise HTTPException(404, "Chat not found.")
    return conversation


def _user_view(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


def _cart_view(db: DbSession, user: User, conversation) -> dict:
    """Cart + recent orders, shaped for the right-hand panel."""
    items = repo.cart_view(db, conversation.id)
    return {
        "cart": {
            "currency": CURRENCY,
            "items": items,
            "subtotal": repo.cart_subtotal(db, conversation.id),
            "coupon_code": conversation.coupon_code,
            "item_count": sum(line["quantity"] for line in items),
        },
        "orders": [
            {"order_id": order.code, "status": repo.order_status(order),
             "total": repo.money(order.total), "eta_minutes": order.eta_minutes}
            for order in repo.list_orders(db, user, limit=5)
        ],
    }
