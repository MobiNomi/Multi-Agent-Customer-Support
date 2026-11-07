import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from app.config import get_settings
from app.database.connection import check_db, init_db
from app.database.seed import seed_if_empty
from app.graph.history import (conversation_log, current_turn_reply, current_turn_sources,
                               current_turn_tool_calls)
from app.graph.workflow import build_support_graph
from app.llm import LLMNotConfiguredError
from app.memory import open_checkpointer
from app.rag.knowledge_base import get_knowledge_base
from app.tools.db_tools import customer_exists, list_customers

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if get_settings().seed_demo_data and seed_if_empty():
        logger.info("Empty database: loaded demo data")
    try:  # build the search index now, so the first chat isn't slow and problems show at startup
        kb = get_knowledge_base()
        logger.info("Knowledge base ready: %d documents, %d chunks", len(kb.doc_ids), len(kb.chunks))
    except FileNotFoundError as exc:
        logger.warning("Knowledge base unavailable: %s", exc)
    # Checkpointer = per-session conversation memory (SQLite by default, survives restarts).
    with open_checkpointer() as checkpointer:
        app.state.graph = build_support_graph(checkpointer=checkpointer)
        logger.info("Chat page: http://127.0.0.1:8000  |  API docs: http://127.0.0.1:8000/docs")
        yield


app = FastAPI(
    title="Multi-Agent Customer Support System",
    description="Customer support API powered by LangGraph and Groq.",
    version="1.4.0",
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
    customer_id: str = Field(..., min_length=1, max_length=64, examples=["CUS-1001"])
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


class DemoCustomer(BaseModel):
    id: str
    name: str
    plan: str
    orders: List[str]


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
def chat_endpoint(request: ChatRequest):
    if not customer_exists(request.customer_id):
        raise HTTPException(status_code=404, detail=f"Unknown customer_id {request.customer_id}")

    session_id = request.session_id or uuid.uuid4().hex
    turn_input = {
        "messages": [HumanMessage(content=request.message)],
        "customer_id": request.customer_id,
        # Per-turn fields start clean even though the conversation persists.
        "ticket_id": None,
        "needs_human": False,
        "resolution": None,
    }

    try:
        output = app.state.graph.invoke(turn_input, _thread_config(request.customer_id, session_id))
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except GraphRecursionError:
        try:
            snapshot = app.state.graph.get_state(_thread_config(request.customer_id, session_id))
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
def session_history(session_id: str, customer_id: str = Query(..., min_length=1, max_length=64)):
    """Earlier messages of a conversation, so a chat window can redraw it after a reload."""
    if not customer_exists(customer_id):
        raise HTTPException(status_code=404, detail=f"Unknown customer_id {customer_id}")
    state = app.state.graph.get_state(_thread_config(customer_id, session_id))
    return SessionHistory(session_id=session_id,
                          messages=conversation_log(state.values.get("messages", [])))


@app.get("/api/v1/demo/customers", response_model=List[DemoCustomer])
def demo_customers():
    """Customer list for the demo switcher. Disabled with DEMO_MODE=false: real apps get the customer from login."""
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    return list_customers()
