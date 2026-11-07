import logging
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from app.config import get_settings
from app.database.connection import check_db, init_db
from app.database.seed import seed_if_empty
from app.graph.history import current_turn_reply
from app.graph.workflow import build_support_graph
from app.llm import LLMNotConfiguredError
from app.tools.db_tools import customer_exists

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if get_settings().seed_demo_data and seed_if_empty():
        logger.info("Empty database: loaded demo data")
    # In-memory checkpointer = per-session conversation memory, lost on restart.
    app.state.graph = build_support_graph(checkpointer=InMemorySaver())
    yield


app = FastAPI(
    title="Multi-Agent Customer Support System",
    description="Customer support API powered by LangGraph and Groq.",
    version="1.1.0",
    lifespan=lifespan,
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000,
                         examples=["I was charged twice on my card for order ORD-1034."])
    customer_id: str = Field(..., min_length=1, max_length=64, examples=["CUS-1001"])
    session_id: Optional[str] = Field(
        default=None, max_length=100,
        description="Send back the session_id from the previous reply to continue the same conversation.",
    )


class ChatResponse(BaseModel):
    response: str
    session_id: str
    assigned_agent: Optional[str]
    intent: Optional[str]
    confidence: Optional[float]
    needs_human: bool
    ticket_id: Optional[str]


@app.get("/health")
def health_check():
    settings = get_settings()
    db_ok = check_db()
    body = {
        "status": "ok" if db_ok else "error",
        "service": "multi-agent-support-api",
        "database": "ok" if db_ok else "unreachable",
        "llm": "configured" if settings.groq_api_key else "missing GROQ_API_KEY",
        "model": settings.llm_model,
    }
    return JSONResponse(body, status_code=200 if db_ok else 503)


# Plain `def`: FastAPI runs it in a worker thread, so the blocking graph call
# doesn't freeze the event loop for other requests.
@app.post("/api/v1/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    if not customer_exists(request.customer_id):
        raise HTTPException(status_code=404, detail=f"Unknown customer_id {request.customer_id}")

    session_id = request.session_id or uuid.uuid4().hex
    config = {
        # Thread is bound to the customer, so a leaked session_id can't open someone else's chat.
        "configurable": {"thread_id": f"{request.customer_id}:{session_id}"},
        "recursion_limit": get_settings().max_graph_steps,
    }
    turn_input = {
        "messages": [HumanMessage(content=request.message)],
        "customer_id": request.customer_id,
        # Per-turn fields start clean even though the conversation persists.
        "ticket_id": None,
        "needs_human": False,
        "resolution": None,
    }

    try:
        output = app.state.graph.invoke(turn_input, config)
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except GraphRecursionError:
        logger.warning("Turn hit the step limit (session %s)", session_id)
        return ChatResponse(
            response=("Sorry, I couldn't finish working on that. Please rephrase, "
                      "or ask for a human agent and I'll pass it on."),
            session_id=session_id, assigned_agent=None, intent=None,
            confidence=None, needs_human=False, ticket_id=None,
        )
    except Exception:
        logger.exception("Chat turn failed (session %s)", session_id)
        raise HTTPException(status_code=500, detail="Internal error while processing the message.")

    return ChatResponse(
        response=current_turn_reply(output.get("messages", []))
        or "Your request has been received and routed to the right team.",
        session_id=session_id,
        assigned_agent=output.get("assigned_agent"),
        intent=output.get("intent"),
        confidence=output.get("confidence"),
        needs_human=output.get("needs_human", False),
        ticket_id=output.get("ticket_id"),
    )
