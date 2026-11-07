"""Look inside the knowledge base.

    python -m scripts.knowledge stats                 documents and chunk sizes
    python -m scripts.knowledge chunks refund-policy  print every chunk of one document
    python -m scripts.knowledge search "money back"   run a search like the agent does
    python -m scripts.knowledge eval                  retrieval quality on evals/retrieval.jsonl
    python -m scripts.knowledge export                write all chunks to data/chunks.jsonl
"""
import argparse
import json
import sys
from collections import Counter

from app.config import PROJECT_ROOT, get_settings
from app.rag.evaluation import evaluate, load_cases
from app.rag.knowledge_base import get_knowledge_base


def cmd_stats(kb, _args):
    sizes = [len(c.text) for c in kb.chunks]
    per_doc = Counter(c.doc_id for c in kb.chunks)
    s = get_settings()
    print(f"Folder: {s.knowledge_dir}")
    print(f"Chunk settings: max {s.chunk_max_chars} chars, overlap {s.chunk_overlap}, top-k {s.retrieval_top_k}")
    print(f"{len(kb.doc_ids)} documents -> {len(kb.chunks)} chunks "
          f"(chars per chunk: min {min(sizes)}, avg {sum(sizes) // len(sizes)}, max {max(sizes)})\n")
    for doc_id in kb.doc_ids:
        print(f"  {per_doc[doc_id]:3d} chunks  {doc_id}")


def cmd_chunks(kb, args):
    chunks = [c for c in kb.chunks if not args.doc or c.doc_id == args.doc]
    if not chunks:
        sys.exit(f"No document called {args.doc!r}. Try: {', '.join(kb.doc_ids)}")
    for c in chunks:
        print(f"=== {c.chunk_id}  [{c.category}]  {len(c.text)} chars\n{c.content}\n")


def cmd_search(kb, args):
    results = kb.search(args.query, k=args.k)
    if not results:
        print("No results.")
    for i, r in enumerate(results, 1):
        print(f"{i}. score {r.score:.2f}  {r.chunk.chunk_id}\n   {r.chunk.header}\n   {r.chunk.text[:200]!r}\n")


def cmd_eval(kb, _args):
    report = evaluate(kb, load_cases(PROJECT_ROOT / "evals" / "retrieval.jsonl"))
    print(f"{report.total} questions   hit@1 {report.hit_at_1:.0%}   hit@3 {report.hit_at_3:.0%}   MRR {report.mrr:.2f}\n")
    for m in report.misses:
        print(f"- rank {m['rank'] or 'not found'}: {m['question']}\n    expected {m['expected']}\n    got      {m['got']}")


def cmd_export(kb, _args):
    out = PROJECT_ROOT / "data" / "chunks.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for c in kb.chunks:
            f.write(json.dumps({"chunk_id": c.chunk_id, "doc_id": c.doc_id, "section": c.section,
                                "category": c.category, "chars": len(c.text), "content": c.content}) + "\n")
    print(f"Wrote {len(kb.chunks)} chunks to {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("chunks"); p.add_argument("doc", nargs="?")
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("-k", type=int, default=4)
    sub.add_parser("eval")
    sub.add_parser("export")
    args = parser.parse_args()
    kb = get_knowledge_base()
    {"stats": cmd_stats, "chunks": cmd_chunks, "search": cmd_search, "eval": cmd_eval, "export": cmd_export}[args.cmd](kb, args)


if __name__ == "__main__":
    main()
