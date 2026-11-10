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

from app.actions.service import ActionError, actions_by_ids
from app.actions.updates import as_message, missing_updates
from app.api import actions as actions_api
from app.api import staff as staff_api
from app.api.conversation import thread_id_for, thread_messages, with_actions
from app.api.schemas import ActionOut
from app.auth.dependencies import COOKIE_NAME, get_current_customer
from app.auth.ratelimit import SlidingWindowLimiter
from app.auth.service import (AccountLocked, AuthUser, EmailTaken, LoginFailed, authenticate,
                              register_customer, validate_registration)
from app.auth.session import start_session
from app.config import get_settings
from app.database.connection import check_db, init_db, session_scope
from app.database.models import Customer
from app.database.seed import (DEMO_ACCOUNTS, DEMO_PASSWORD, DEMO_STAFF, ensure_demo_passwords, ensure_demo_payments,
                               ensure_demo_staff, seed_if_empty)
from app.graph.history import (current_turn_action_ids, current_turn_reply, current_turn_retrieved,
                               current_turn_sources, current_turn_tool_calls)
from app.graph.workflow import build_support_graph
from app.guardrails.notes import notes as guardrail_notes
from app.guardrails.notes import record as record_guardrails
from app.guardrails.sensitive import mask_sensitive
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
    if settings.demo_mode and ensure_demo_staff():
        logger.info("Added the demo staff account %s", DEMO_STAFF["email"])
    if settings.demo_mode and ensure_demo_payments():
        logger.info("Added new demo payments (Sara's duplicate charge on ORD-1035)")
    if not settings.auth_secret:
        logger.warning("AUTH_SECRET is not set: everyone is signed out whenever the server restarts. "
                       "Set AUTH_SECRET in .env.")
    try:  # build the search index now, so the first chat isn't slow and problems show at startup
        kb = get_knowledge_base()
        logger.info("Knowledge base ready: %d documents, %d chunks", len(kb.doc_ids), len(kb.chunks))
    except FileNotFoundError as exc:
        logger.warning("Knowledge base unavailable: %s", exc)
    app.state.registration_limiter = SlidingWindowLimiter(settings.registrations_per_hour, 3600)
    app.state.chat_limiter = SlidingWindowLimiter(settings.chat_messages_per_minute, 60)
    # Checkpointer = per-session conversation memory (SQLite by default, survives restarts).
    with open_checkpointer() as checkpointer:
        app.state.graph = build_support_graph(checkpointer=checkpointer)
        logger.info("Chat page: http://127.0.0.1:8000  |  Staff console: http://127.0.0.1:8000/staff  |  "
                    "API docs: http://127.0.0.1:8000/docs")
        yield


app = FastAPI(
    title="Multi-Agent Customer Support System",
    description="Customer support API powered by LangGraph and Groq.",
    version="1.8.0",
    lifespan=lifespan,
)

if get_settings().cors_origins:  # only needed when the chat UI is hosted on another domain
    app.add_middleware(CORSMiddleware, allow_origins=list(get_settings().cors_origins),
                       allow_methods=["GET", "POST"], allow_headers=["*"])

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(actions_api.router)
app.include_router(staff_api.router)


@app.exception_handler(ActionError)
def action_error(request: Request, exc: ActionError):
    """Refused or impossible steps on a request (not found, already decided, expired, missing reason)."""
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


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


class GuardrailNote(BaseModel):
    stage: str = Field(description="input, router, action or output")
    kind: str
    message: str


class ChatResponse(BaseModel):
    response: str
    session_id: str
    assigned_agent: Optional[str]
    intent: Optional[str]
    confidence: Optional[float]
    needs_human: bool
    ticket_id: Optional[str]
    sources: List[str] = Field(default_factory=list,
                               description="Document sections the reply cites.")
    retrieved: List[str] = Field(default_factory=list,
                                 description="Every document section the agent looked up for this reply.")
    tools_used: List[ToolUse] = Field(default_factory=list,
                                      description="Tools the agent called for this answer, in order.")
    guardrails: List[GuardrailNote] = Field(default_factory=list,
                                            description="Safety checks that changed or stopped something.")
    message_masked: Optional[str] = Field(default=None,
                                          description="Your message as it was sent and saved, if sensitive "
                                                      "details were hidden.")
    actions: List[ActionOut] = Field(default_factory=list,
                                     description="Changes this reply proposed. Each needs the customer's "
                                                 "confirmation: POST /api/v1/actions/{id}/confirm.")


class HistoryMessage(BaseModel):
    role: Literal["customer", "assistant", "support"]
    content: str
    actions: Optional[List[ActionOut]] = Field(default=None, description="Requests this reply proposed.")
    update_id: Optional[int] = None
    kind: Optional[str] = None
    author: Optional[str] = Field(default=None, description="For support messages: the staff member's name.")
    created_at: Optional[str] = None


class SessionHistory(BaseModel):
    session_id: str
    messages: List[HistoryMessage]
    last_update_id: int = Field(0, description="Poll /sessions/{id}/updates?after=<this> for newer support messages.")


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
    role: str = "customer"


def _account(user: AuthUser) -> Account:
    return Account(id=user.id, name=user.name, email=user.email, plan=user.plan, orders=user.orders)


# ---------- pages ----------

@app.get("/", include_in_schema=False)
def chat_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/staff", include_in_schema=False)
def staff_console_page():
    return FileResponse(STATIC_DIR / "staff.html")


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
    return {"configurable": {"thread_id": thread_id_for(customer_id, session_id)},
            "recursion_limit": get_settings().max_graph_steps}


# Plain `def`: FastAPI runs it in a worker thread, so the blocking graph call
# doesn't freeze the event loop for other requests.
@app.post("/api/v1/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest, user: AuthUser = Depends(get_current_customer)):
    # The customer comes from the signed-in session, never from the request body.
    customer_id = user.id
    session_id = request.session_id or uuid.uuid4().hex
    settings = get_settings()

    message, masked_kinds = request.message, []
    if settings.guardrails_enabled:
        allowed, retry_after = app.state.chat_limiter.hit(customer_id)
        if not allowed:
            raise HTTPException(status_code=429,
                                detail=f"You're sending messages too quickly. Wait {retry_after} seconds and try again.",
                                headers={"Retry-After": str(retry_after)})
        # Hide card numbers, passwords etc. before the message is sent to the model or saved anywhere.
        message, masked_kinds = mask_sensitive(request.message)
        record_guardrails(masked_kinds, customer_id, f"{customer_id}:{session_id}")

    # Staff decisions and account updates since the last message go into the conversation memory first,
    # so the assistant knows that, say, the refund was approved.
    config = _thread_config(customer_id, session_id)
    try:
        current = app.state.graph.get_state(config).values.get("messages", [])
        updates = [as_message(u) for u in missing_updates(config["configurable"]["thread_id"], current)]
    except Exception:
        logger.exception("Could not load support updates (session %s)", session_id)
        updates = []

    turn_input = {
        "messages": updates + [HumanMessage(content=message)],
        "customer_id": customer_id,
        # Per-turn fields start clean even though the conversation persists.
        "ticket_id": None,
        "needs_human": False,
        "resolution": None,
        "intent": None,
        "assigned_agent": None,
        "confidence": None,
        "guardrail_notes": guardrail_notes(masked_kinds),
    }

    try:
        output = app.state.graph.invoke(turn_input, config)
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except GraphRecursionError:
        try:
            snapshot = app.state.graph.get_state(config)
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
        retrieved=current_turn_retrieved(messages),
        tools_used=current_turn_tool_calls(messages),
        guardrails=output.get("guardrail_notes") or [],
        message_masked=message if masked_kinds else None,
        actions=actions_by_ids(current_turn_action_ids(messages), customer_id),
    )


@app.get("/api/v1/sessions/{session_id}/messages", response_model=SessionHistory,
         response_model_exclude_none=True)
def session_history(session_id: str, user: AuthUser = Depends(get_current_customer)):
    """One of the signed-in customer's conversations, to redraw the chat after a reload.

    Includes support messages and, under each reply that proposed a change, that request's current status.
    """
    log, last_update_id = thread_messages(app.state.graph, thread_id_for(user.id, session_id))
    return SessionHistory(session_id=session_id, messages=with_actions(log, user.id),
                          last_update_id=last_update_id)


@app.get("/api/v1/demo/accounts", response_model=List[DemoAccount])
def demo_accounts():
    """Demo sign-ins shown on the sign-in pages (role "staff" is for the console). Off with DEMO_MODE=false."""
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    accounts = []
    with session_scope() as session:
        for account_id in (*DEMO_ACCOUNTS, DEMO_STAFF["id"]):
            c = session.get(Customer, account_id)
            if c is not None and c.password_hash:
                accounts.append(DemoAccount(name=c.name, email=c.email, plan=c.plan, password=DEMO_PASSWORD,
                                            role=c.role or "customer"))
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
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="This is a staff account. Sign in on the staff console at /staff.")

    return _start_session(response, user, token_version)


def _start_session(response: Response, user: AuthUser, token_version: int) -> LoginResponse:
    """Create the signed token, set it as the session cookie, and return it."""
    token, expires = start_session(response, user, token_version, COOKIE_NAME)
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
def me(user: AuthUser = Depends(get_current_customer)):
    """The signed-in customer. 401 if nobody is signed in."""
    return _account(user)
