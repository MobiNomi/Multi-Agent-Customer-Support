"""Measure retrieval quality against questions with known answers (evals/retrieval.jsonl).

Each case lists the sections that correctly answer it, as "doc_id::Section name". A result counts
as right when its document matches and its section path contains that section name.

Metrics:
- hit@1 / hit@3: share of questions where a right chunk is the first result / in the top 3.
- MRR (mean reciprocal rank): 1.0 if a right chunk is always first, 0.5 if always second, and so on.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.rag.knowledge_base import KnowledgeBase, SearchResult


@dataclass
class EvalCase:
    question: str
    answers: List[str]  # "doc_id::Section name"

    def matches(self, result: SearchResult) -> bool:
        for answer in self.answers:
            doc_id, section = answer.split("::", 1)
            if result.chunk.doc_id == doc_id and section.lower() in result.chunk.section.lower():
                return True
        return False


@dataclass
class EvalReport:
    total: int
    hit_at_1: float
    hit_at_3: float
    mrr: float
    misses: List[dict]


def load_cases(path: Path) -> List[EvalCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [EvalCase(**json.loads(line)) for line in lines if line.strip()]


def evaluate(kb: KnowledgeBase, cases: List[EvalCase], k: int = 5) -> EvalReport:
    hits1 = hits3 = 0
    rr_total = 0.0
    misses = []
    for case in cases:
        results = kb.search(case.question, k=k)
        rank: Optional[int] = next((i + 1 for i, r in enumerate(results) if case.matches(r)), None)
        hits1 += rank == 1
        hits3 += bool(rank and rank <= 3)
        rr_total += 1 / rank if rank else 0.0
        if rank != 1:
            misses.append({
                "question": case.question,
                "expected": case.answers[0],
                "rank": rank,
                "got": [f"{r.chunk.doc_id}::{r.chunk.section}" for r in results[:3]],
            })
    n = len(cases) or 1
    return EvalReport(total=len(cases), hit_at_1=hits1 / n, hit_at_3=hits3 / n, mrr=rr_total / n, misses=misses)
