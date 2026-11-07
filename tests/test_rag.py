from pathlib import Path

import pytest

from app import config
from app.rag.bm25 import BM25Index, tokenize, tokenize_query
from app.rag.chunking import chunk_document, split_long, split_sections
from app.rag.documents import Document, clean_text, load_documents, parse_frontmatter
from app.rag.knowledge_base import KnowledgeBase, get_knowledge_base
from app.tools.knowledge_tools import search_knowledge_base


def make_doc(text, doc_id="doc", title="Doc", category="general"):
    return Document(doc_id=doc_id, title=title, category=category, updated=None, text=text, source=f"{doc_id}.md")


# ---------- loading ----------

def test_parse_frontmatter():
    meta, body = parse_frontmatter("---\ntitle: Refunds\ncategory: Billing\n---\n# Refunds\nBody")
    assert meta == {"title": "Refunds", "category": "Billing"}
    assert body.strip() == "# Refunds\nBody"


def test_no_frontmatter_keeps_body():
    meta, body = parse_frontmatter("# Just text")
    assert meta == {} and body == "# Just text"


def test_clean_text_normalises_whitespace():
    assert clean_text("a  \r\nb\n\n\n\n\nc\t") == "a\nb\n\nc"


def test_load_documents(tmp_path: Path):
    (tmp_path / "one.md").write_text("---\ntitle: One\ncategory: Billing\n---\n# One\n\nHello", encoding="utf-8")
    (tmp_path / "two.txt").write_text("# Second doc\n\nPlain text file", encoding="utf-8")
    (tmp_path / "empty.md").write_text("---\ntitle: Empty\n---\n", encoding="utf-8")
    (tmp_path / "skip.pdf").write_bytes(b"%PDF")
    docs = load_documents(tmp_path)
    assert [(d.doc_id, d.title, d.category) for d in docs] == [("one", "One", "billing"),
                                                              ("two", "Second doc", "general")]


def test_missing_folder_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_documents(tmp_path / "nope")


# ---------- chunking ----------

def test_sections_follow_heading_path():
    doc = make_doc("# Title\nIntro text\n## Errors\nGeneral\n### 401\nBad key\n### 429\nSlow down\n## Webhooks\nPOST")
    assert split_sections(doc) == [
        ("Introduction", "Intro text"),
        ("Errors", "General"),
        ("Errors > 401", "Bad key"),
        ("Errors > 429", "Slow down"),
        ("Webhooks", "POST"),
    ]


def test_headings_inside_code_fences_are_ignored():
    doc = make_doc("## Setup\n```\n# not a heading\n```\nDone")
    assert split_sections(doc) == [("Setup", "```\n# not a heading\n```\nDone")]


def test_split_long_respects_size_and_overlaps():
    paragraphs = [f"Paragraph {i} " + "word " * 30 for i in range(8)]   # ~170 chars each
    text = "\n\n".join(p.strip() for p in paragraphs)
    pieces = split_long(text, max_chars=500, overlap=200)
    assert len(pieces) > 1
    assert all(len(p) <= 500 for p in pieces)
    # every paragraph survives, and consecutive pieces share their boundary paragraph
    for para in paragraphs:
        assert any(para.strip() in p for p in pieces)
    for a, b in zip(pieces, pieces[1:]):
        assert a.split("\n\n")[-1] == b.split("\n\n")[0]


def test_split_long_falls_back_to_sentences():
    text = " ".join(f"Sentence number {i} is here." for i in range(60))  # one long paragraph
    pieces = split_long(text, max_chars=300, overlap=0)
    assert all(len(p) <= 300 for p in pieces)
    assert all(p.endswith(".") for p in pieces)


def test_short_text_is_one_piece():
    assert split_long("short", 100, 10) == ["short"]


def test_chunk_document_ids_and_headers():
    doc = make_doc("## A\none\n## B\ntwo", doc_id="refunds", title="Refund policy")
    chunks = chunk_document(doc)
    assert [c.chunk_id for c in chunks] == ["refunds#0", "refunds#1"]
    assert chunks[1].content == "Refund policy > B\n\ntwo"


# ---------- BM25 ----------

def test_tokenize_stems_and_drops_stopwords():
    assert tokenize("The refunds were refunded") == ["refund", "refund"]


def test_query_synonyms_expand():
    assert "annual" in tokenize_query("yearly plan")


def test_bm25_prefers_rare_matching_terms():
    docs = [tokenize("refund policy for annual plans"), tokenize("plans and pricing"), tokenize("api error codes")]
    scores = BM25Index(docs).scores(tokenize("annual refund"))
    assert scores[0] > scores[1] == scores[2] == 0


# ---------- knowledge base ----------

def tiny_kb():
    return KnowledgeBase(
        chunk_document(make_doc("## Refunds\nAnnual plans refunded within 14 days.", "refund-policy",
                                "Refund policy", "billing"))
        + chunk_document(make_doc("## Errors\n401 means the API key is wrong.", "api", "API guide", "technical"))
    )


def test_search_finds_right_chunk_and_filters():
    kb = tiny_kb()
    assert kb.search("refund annual")[0].chunk.doc_id == "refund-policy"
    assert kb.search("refund annual", category="technical") == []
    assert kb.search("the and of") == []        # only stopwords
    assert kb.search("kubernetes") == []        # nothing matches


def test_real_knowledge_folder_builds():
    kb = get_knowledge_base()
    assert len(kb.doc_ids) >= 10
    assert all(len(c.text) <= config.get_settings().chunk_max_chars for c in kb.chunks)
    assert len({c.chunk_id for c in kb.chunks}) == len(kb.chunks)


def test_tool_returns_sources_and_text():
    out = search_knowledge_base.invoke({"query": "API error 401 unauthorized"})
    top = out["results"][0]
    assert top["source"] == "API guide and error codes > Error codes > 401 Unauthorized"
    assert "Authorization: Bearer" in top["text"]
    assert len(out["results"]) <= config.get_settings().retrieval_top_k


def test_tool_with_no_match_says_not_to_guess():
    out = search_knowledge_base.invoke({"query": "zzzqqq"})
    assert out["results"] == [] and "Do not guess" in out["note"]
