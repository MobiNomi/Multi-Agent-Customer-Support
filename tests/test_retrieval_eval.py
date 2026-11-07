"""Quality gate: fails if a change to the documents, chunking or search makes retrieval worse.

Run `python -m scripts.knowledge eval` to see the numbers and every miss.
"""
from app.config import PROJECT_ROOT
from app.rag.evaluation import evaluate, load_cases
from app.rag.knowledge_base import get_knowledge_base


def test_retrieval_quality_does_not_regress():
    report = evaluate(get_knowledge_base(), load_cases(PROJECT_ROOT / "evals" / "retrieval.jsonl"))
    assert report.total >= 30
    assert report.hit_at_3 >= 0.95, report.misses
    assert report.hit_at_1 >= 0.85, report.misses
    assert report.mrr >= 0.90, report.misses
