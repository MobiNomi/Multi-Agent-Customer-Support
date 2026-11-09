"""Check an agent's reply before the customer sees it."""
import re
from typing import Iterable, List, Tuple

from app.guardrails.sensitive import mask_sensitive

CITATION = re.compile(r"\s?\[([^\[\]\n]+? > [^\[\]\n]+?)\]")
REFUND_DONE = [
    re.compile(r"\b(?:refund|money|amount|payment)\b[^.!?\n]{0,50}?\b(?:has|have) been\s+(?:fully\s+)?"
               r"(?:processed|issued|sent|completed|credited|returned|refunded|reversed|approved|transferred)\b", re.I),
    re.compile(r"\b(?:I|we)(?:'ve| have)?\s+(?:already\s+)?(?:processed|issued|sent|completed|approved|initiated "
               r"and completed)\s+(?:your|the|a|full)\s+(?:full\s+)?refund\b", re.I),
    re.compile(r"\b(?:I|we)(?:'ve| have)\s+(?:already\s+)?refunded\b", re.I),
    re.compile(r"\byou(?:'ve| have) been refunded\b", re.I),
]
SAFE_REFUND_SENTENCE = ("I've passed your refund request to the billing team; they review requests within "
                        "1 business day.")
UNSUPPORTED_REPLY = ("I couldn't confirm that from our help articles. Would you like me to connect you with "
                     "a human agent?")
LEAK_REPLY = ("I can't share details about how I'm set up, but I'm happy to help with any question about "
              "AcmeCloud or your account.")
LEAK_MARKERS = ("current customer id:", "rules for every reply", "you have reached the tool limit")
INTERNAL_NAMES = {
    "search_knowledge_base": "our help articles",
    "get_order_payments": "your payment records",
    "get_customer_details": "your account details",
    "create_support_ticket": "a support ticket",
    "tool_executor": "our systems",
}
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _shingles(text: str, n: int = 8) -> set:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


class OutputGuard:
    """Built once from the prompts the reply must never repeat."""

    def __init__(self, secret_texts: Iterable[str]):
        self.secret_shingles = set().union(*(_shingles(t) for t in secret_texts)) if secret_texts else set()

    def leaks_prompt(self, reply: str) -> bool:
        low = reply.lower()
        return any(m in low for m in LEAK_MARKERS) or bool(_shingles(reply) & self.secret_shingles)

    def check(self, reply: str, retrieved_sources: List[str]) -> Tuple[str, List[str]]:
        """Return (safe reply, kinds of fixes applied)."""
        kinds: List[str] = []
        if self.leaks_prompt(reply):
            return LEAK_REPLY, ["leak_blocked"]

        # Citations must point at sections the agent actually retrieved this turn. A sentence whose only
        # sources are made up is an unsupported claim, so the whole sentence goes, not just the citation.
        allowed = set(retrieved_sources)
        kept = []
        for sentence in _SENTENCE.split(reply):
            cited = [c.strip() for c in CITATION.findall(sentence)]
            bad = [c for c in cited if c not in allowed]
            if not bad:
                kept.append(sentence)
                continue
            kinds.append("citation_removed")
            if len(bad) < len(cited):        # keep the sentence, drop only the made-up citations
                kept.append(CITATION.sub(lambda m: m.group(0) if m.group(1).strip() in allowed else "", sentence))
        reply = " ".join(kept) if kept else UNSUPPORTED_REPLY

        # The bot can only open refund requests; it must never say money was returned.
        sentences = _SENTENCE.split(reply)
        fixed, replaced = [], False
        for sentence in sentences:
            if any(p.search(sentence) for p in REFUND_DONE):
                if not replaced:
                    fixed.append(SAFE_REFUND_SENTENCE)
                replaced = True
            else:
                fixed.append(sentence)
        if replaced:
            kinds.append("promise_rewritten")
            reply = " ".join(fixed)

        for name, plain in INTERNAL_NAMES.items():
            if name in reply:
                reply = re.sub(rf"`?{name}`?(?:\(\))?", plain, reply)
                kinds.append("internal_names_replaced")

        reply, masked = mask_sensitive(reply)
        if masked:
            kinds.append("reply_masked")

        reply = re.sub(r"[ \t]+([.,;:!?])", r"\1", reply)    # tidy spaces left by removed citations
        return reply.strip(), list(dict.fromkeys(kinds))
