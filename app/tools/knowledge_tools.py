"""The RAG tool: lets an agent look up AcmeCloud's own documents before answering."""
from typing import Any, Dict

from langchain_core.tools import tool

from app.config import get_settings
from app.rag.knowledge_base import get_knowledge_base

NO_RESULTS_NOTE = ("No relevant documents found. Do not guess: tell the customer you don't have that "
                   "information and offer to connect them with a human agent.")


@tool
def search_knowledge_base(query: str) -> Dict[str, Any]:
    """Search AcmeCloud's help documents: plans and pricing, billing, refund policy, cancellations, orders,
    account security, API errors, troubleshooting, privacy and FAQs. Use a short, specific query such as
    'refund annual plan' or 'error 429'. One search is usually enough; search again only with clearly
    different words, and if the results still don't answer the question, say so instead of searching more."""
    results = get_knowledge_base().search(query, k=get_settings().retrieval_top_k)
    if not results:
        return {"results": [], "note": NO_RESULTS_NOTE}
    return {
        "results": [
            {
                "source": r.chunk.header,
                "chunk_id": r.chunk.chunk_id,
                "score": round(r.score, 2),
                "text": r.chunk.text,
            }
            for r in results
        ]
    }
