import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from app.auth.dependencies import COOKIE_NAME, get_current_user
from app.auth.ratelimit import SlidingWindowLimiter
from app.auth.service import (AccountLocked, AuthUser, EmailTaken, LoginFailed, authenticate,
                              register_customer, validate_registration)
from app.auth.tokens import create_access_token
from app.config import get_settings
from app.database.connection import check_db, init_db, session_scope
from app.database.models import Customer
from app.database.seed import DEMO_ACCOUNTS, DEMO_PASSWORD, ensure_demo_passwords, seed_if_empty
from app.graph.history import (conversation_log, current_turn_reply, current_turn_sources,
                               current_turn_tool_calls)
from app.graph.workflow import build_support_graph
from app.llm import LLMNotConfiguredError
from app.memory import open_checkpointer
from app.rag.knowledge_base import get_knowledge_base

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)   # don't log every Groq HTTP call
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    if settings.seed_demo_data and seed_if_empty():
        logger.info("Empty database: loaded demo data")
    if settings.demo_mode and ensure_demo_passwords():
        logger.info("Demo accounts can now sign in with the demo password")
    if not settings.auth_secret:
        logger.warning("AUTH_SECRET is not set: everyone is signed out whenever the server restarts. "
                       "Set AUTH_SECRET in .env.")
    try:  # build the search index now, so the first chat isn't slow and problems show at startup
        kb = get_knowledge_base()
        logger.info("Knowledge base ready: %d documents, %d chunks", len(kb.doc_ids), len(kb.chunks))
    except FileNotFoundError as exc:
        logger.warning("Knowledge base unavailable: %s", exc)
    app.state.registration_limiter = SlidingWindowLimiter(settings.registrations_per_hour, 3600)
    # Checkpointer = per-session conversation memory (SQLite by default, survives restarts).
    with open_checkpointer() as checkpointer:
        app.state.graph = build_support_graph(checkpointer=checkpointer)
        logger.info("Chat page: http://127.0.0.1:8000  |  API docs: http://127.0.0.1:8000/docs")
        yield


app = FastAPI(
    title="Multi-Agent Customer Support System",
    description="Customer support API powered by LangGraph and Groq.",
    version="1.5.0",
    lifespan=lifespan,
)

if get_settings().cors_origins:  # only needed when the chat UI is hosted on another domain
    app.add_middleware(CORSMiddleware, allow_origins=list(get_settings().cors_origins),
                       allow_methods=["GET", "POST"], allow_headers=["*"])

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------- models ----------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000,
                         examples=["I was charged twice on my card for order ORD-1034."])
    session_id: Optional[str] = Field(
        default=None, max_length=100,
        description="Send back the session_id from the previous reply to continue the same conversation.",
    )


class ToolUse(BaseModel):
    name: str
    args: Dict[str, Any]


class ChatResponse(BaseModel):
    response: str
    session_id: str
    assigned_agent: Optional[str]
    intent: Optional[str]
    confidence: Optional[float]
    needs_human: bool
    ticket_id: Optional[str]
    sources: List[str] = Field(default_factory=list,
                               description="Company documents the answer is based on.")
    tools_used: List[ToolUse] = Field(default_factory=list,
                                      description="Tools the agent called for this answer, in order.")


class HistoryMessage(BaseModel):
    role: Literal["customer", "assistant"]
    content: str


class SessionHistory(BaseModel):
    session_id: str
    messages: List[HistoryMessage]


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254, examples=["ali@example.com"])
    password: str = Field(..., min_length=1, max_length=256, examples=["demo1234"])


class RegisterRequest(BaseModel):
    name: str = Field(..., max_length=200, examples=["Ayesha Malik"])
    email: str = Field(..., max_length=254, examples=["ayesha@example.com"])
    password: str = Field(..., max_length=256, examples=["a-long-password"])


class AuthOptions(BaseModel):
    registration_enabled: bool


class Account(BaseModel):
    id: str
    name: str
    email: str
    plan: str
    orders: List[str]


class LoginResponse(BaseModel):
    customer: Account
    access_token: str = Field(description="Also set as an HttpOnly cookie. API clients can send it as "
                                          "'Authorization: Bearer <token>'.")
    token_type: str = "bearer"
    expires_at: datetime


class DemoAccount(BaseModel):
    name: str
    email: str
    plan: str
    password: str


def _account(user: AuthUser) -> Account:
    return Account(id=user.id, name=user.name, email=user.email, plan=user.plan, orders=user.orders)


# ---------- page ----------

@app.get("/", include_in_schema=False)
def chat_page():
    return FileResponse(STATIC_DIR / "index.html")


# ---------- API ----------

@app.get("/health")
def health_check():
    settings = get_settings()
    db_ok = check_db()
    try:
        kb = get_knowledge_base()
        knowledge = f"{len(kb.doc_ids)} documents, {len(kb.chunks)} chunks"
    except FileNotFoundError:
        knowledge = "missing knowledge folder"
    body = {
        "status": "ok" if db_ok else "error",
        "service": "multi-agent-support-api",
        "database": "ok" if db_ok else "unreachable",
        "llm": "configured" if settings.groq_api_key else "missing GROQ_API_KEY",
        "model": settings.llm_model,
        "memory": settings.memory_backend,
        "knowledge": knowledge,
    }
    return JSONResponse(body, status_code=200 if db_ok else 503)


def _thread_config(customer_id: str, session_id: str) -> dict:
    # Thread is bound to the customer, so a leaked session_id can't open someone else's chat.
    return {"configurable": {"thread_id": f"{customer_id}:{session_id}"},
            "recursion_limit": get_settings().max_graph_steps}


# Plain `def`: FastAPI runs it in a worker thread, so the blocking graph call
# doesn't freeze the event loop for other requests.
@app.post("/api/v1/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest, user: AuthUser = Depends(get_current_user)):
    # The customer comes from the signed-in session, never from the request body.
    customer_id = user.id
    session_id = request.session_id or uuid.uuid4().hex
    turn_input = {
        "messages": [HumanMessage(content=request.message)],
        "customer_id": customer_id,
        # Per-turn fields start clean even though the conversation persists.
        "ticket_id": None,
        "needs_human": False,
        "resolution": None,
    }

    try:
        output = app.state.graph.invoke(turn_input, _thread_config(customer_id, session_id))
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except GraphRecursionError:
        try:
            snapshot = app.state.graph.get_state(_thread_config(customer_id, session_id))
            tools = [c["name"] for c in current_turn_tool_calls(snapshot.values.get("messages", []))]
        except Exception:
            tools = ["(unavailable)"]
        logger.warning("Turn hit the step limit (session %s). Tools called: %s", session_id, tools)
        return ChatResponse(
            response=("Sorry, I couldn't finish working on that. Please rephrase, "
                      "or ask for a human agent and I'll pass it on."),
            session_id=session_id, assigned_agent=None, intent=None,
            confidence=None, needs_human=False, ticket_id=None,
        )
    except Exception:
        logger.exception("Chat turn failed (session %s)", session_id)
        raise HTTPException(status_code=500, detail="Internal error while processing the message.")

    messages = output.get("messages", [])
    return ChatResponse(
        response=current_turn_reply(messages) or "Your request has been received and routed to the right team.",
        session_id=session_id,
        assigned_agent=output.get("assigned_agent"),
        intent=output.get("intent"),
        confidence=output.get("confidence"),
        needs_human=output.get("needs_human", False),
        ticket_id=output.get("ticket_id"),
        sources=current_turn_sources(messages),
        tools_used=current_turn_tool_calls(messages),
    )


@app.get("/api/v1/sessions/{session_id}/messages", response_model=SessionHistory)
def session_history(session_id: str, user: AuthUser = Depends(get_current_user)):
    """Earlier messages of one of the signed-in customer's conversations, to redraw the chat after a reload."""
    state = app.state.graph.get_state(_thread_config(user.id, session_id))
    return SessionHistory(session_id=session_id,
                          messages=conversation_log(state.values.get("messages", [])))


@app.get("/api/v1/demo/accounts", response_model=List[DemoAccount])
def demo_accounts():
    """Demo sign-ins shown on the sign-in page. Disabled with DEMO_MODE=false."""
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    accounts = []
    for customer_id in DEMO_ACCOUNTS:
        with session_scope() as session:
            c = session.get(Customer, customer_id)
            if c is not None and c.password_hash:
                accounts.append(DemoAccount(name=c.name, email=c.email, plan=c.plan, password=DEMO_PASSWORD))
    return accounts


# ---------- sign-in ----------

@app.post("/api/v1/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, response: Response):
    """Check email and password; on success set the session cookie and return the token."""
    try:
        user, token_version = authenticate(body.email, body.password)
    except AccountLocked as exc:
        minutes = max(1, round(exc.retry_after_seconds / 60))
        raise HTTPException(status_code=429,
                            detail=f"Too many failed sign-in attempts. Try again in {minutes} "
                                   f"minute{'s' if minutes != 1 else ''}.",
                            headers={"Retry-After": str(exc.retry_after_seconds)})
    except LoginFailed:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")

    return _start_session(response, user, token_version)


def _start_session(response: Response, user: AuthUser, token_version: int) -> LoginResponse:
    """Create the signed token, set it as the session cookie, and return it."""
    settings = get_settings()
    token, expires = create_access_token(user.id, token_version, user.role)
    response.set_cookie(COOKIE_NAME, token, max_age=settings.access_token_minutes * 60,
                        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    return LoginResponse(customer=_account(user), access_token=token, expires_at=expires)


@app.post("/api/v1/auth/register", response_model=LoginResponse, status_code=201)
def register(body: RegisterRequest, request: Request, response: Response):
    """Create a customer account (Free plan) and sign it in."""
    if not get_settings().registration_enabled:
        raise HTTPException(status_code=403, detail="New sign-ups are turned off. Contact support for an account.")
    try:  # typos don't count towards the limit; only real attempts do
        validate_registration(body.name, body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    ip = request.client.host if request.client else "unknown"
    allowed, retry_after = app.state.registration_limiter.hit(ip)
    if not allowed:
        minutes = max(1, round(retry_after / 60))
        raise HTTPException(status_code=429,
                            detail=f"Too many new accounts from this network. Try again in {minutes} "
                                   f"minute{'s' if minutes != 1 else ''}.",
                            headers={"Retry-After": str(retry_after)})
    try:
        user, token_version = register_customer(body.name, body.email, body.password)
    except EmailTaken:
        raise HTTPException(status_code=409, detail="An account with this email already exists. Sign in instead.")
    logger.info("New customer registered: %s", user.id)
    response.status_code = 201
    return _start_session(response, user, token_version)


@app.get("/api/v1/auth/options", response_model=AuthOptions)
def auth_options():
    """What the sign-in page should offer."""
    return AuthOptions(registration_enabled=get_settings().registration_enabled)


@app.post("/api/v1/auth/logout", status_code=204)
def logout():
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/v1/auth/me", response_model=Account)
def me(user: AuthUser = Depends(get_current_user)):
    """The signed-in customer. 401 if nobody is signed in."""
    return _account(user)
