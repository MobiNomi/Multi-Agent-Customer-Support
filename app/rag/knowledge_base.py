"""Step 4: the search API the agents use. Built once at startup from the knowledge folder."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from app.config import get_settings
from app.rag.bm25 import BM25Index, tokenize, tokenize_query
from app.rag.chunking import Chunk, chunk_documents
from app.rag.documents import load_documents

HEADER_WEIGHT = 2  # words in "Title > Section" count double: headings say what a chunk is about


@dataclass(frozen=True)
class SearchResult:
    chunk: Chunk
    score: float


class KnowledgeBase:
    def __init__(self, chunks: List[Chunk]):
        self.chunks = chunks
        self.doc_ids = sorted({c.doc_id for c in chunks})
        self.index = BM25Index([tokenize(c.header) * HEADER_WEIGHT + tokenize(c.text) for c in chunks])

    @classmethod
    def from_folder(cls, folder: Path, max_chars: int = 1200, overlap: int = 150) -> "KnowledgeBase":
        return cls(chunk_documents(load_documents(folder), max_chars, overlap))

    def search(self, query: str, k: int = 4, category: Optional[str] = None,
               min_relative_score: float = 0.2) -> List[SearchResult]:
        """Top-k chunks for the query. Chunks scoring under 20% of the best match are dropped as noise."""
        tokens = tokenize_query(query)
        if not tokens or not self.chunks:
            return []
        ranked = sorted(
            (SearchResult(chunk, score) for chunk, score in zip(self.chunks, self.index.scores(tokens))
             if score > 0 and (category is None or chunk.category == category)),
            key=lambda r: r.score, reverse=True,
        )
        if not ranked:
            return []
        cutoff = ranked[0].score * min_relative_score
        return [r for r in ranked[:k] if r.score >= cutoff]


@lru_cache(maxsize=1)
def get_knowledge_base() -> KnowledgeBase:
    s = get_settings()
    return KnowledgeBase.from_folder(s.knowledge_dir, s.chunk_max_chars, s.chunk_overlap)
