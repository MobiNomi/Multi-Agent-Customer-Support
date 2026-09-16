import os
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

load_dotenv()

from app.graph.workflow import build_support_graph

app = FastAPI(
    title="Multi-Agent Customer Support System",
    description="Enterprise-grade customer support API powered by LangGraph & Groq.",
    version="1.0.0"
)

# Compile graph on startup
graph = build_support_graph()


class ChatRequest(BaseModel):
    message: str = Field(..., example="I was charged twice on my card for order ORD-1034.")
    customer_id: Optional[str] = Field(default="CUS-1001", example="CUS-1001")


class ChatResponse(BaseModel):
    response: str
    assigned_agent: Optional[str]
    intent: Optional[str]
    needs_human: bool
    ticket_id: Optional[str]


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "multi-agent-support-api"}


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    try:
        initial_input = {
            "messages": [HumanMessage(content=request.message)],
            "customer_id": request.customer_id
        }

        # Run state graph workflow
        output = graph.invoke(initial_input)

        # Extract final AI response message
        final_message = ""
        for msg in reversed(output.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                final_message = msg.content
                break

        if not final_message:
            final_message = "Your query has been processed and routed appropriately."

        return ChatResponse(
            response=final_message,
            assigned_agent=output.get("assigned_agent"),
            intent=output.get("intent"),
            needs_human=output.get("needs_human", False),
            ticket_id=output.get("ticket_id")
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))