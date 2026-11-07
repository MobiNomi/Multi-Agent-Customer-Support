"""Step 2: cut each document into small, self-contained chunks.

Strategy (heading-aware chunking):
1. Split on markdown headings, so each chunk covers one topic, e.g. "Refund policy > Annual plans".
2. If a section is longer than `max_chars`, split it on paragraphs, then lines, then sentences,
   carrying the last ~`overlap` characters into the next piece so no fact is cut in half.
3. Prefix every chunk with its "Title > Section" header. The chunk then makes sense on its own,
   both to the search index and to the model reading it.
"""
import re
from dataclasses import dataclass
from typing import List, Tuple

from app.rag.documents import Document

HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str        # "<doc_id>#<n>"
    doc_id: str
    title: str
    section: str         # heading path, e.g. "Error codes > 401 Unauthorized"
    category: str
    text: str            # chunk body
    source: str

    @property
    def header(self) -> str:
        return f"{self.title} > {self.section}"

    @property
    def content(self) -> str:
        """What gets indexed and what the model reads."""
        return f"{self.header}\n\n{self.text}"


def split_sections(doc: Document) -> List[Tuple[str, str]]:
    """Return (section path, body) pairs. Headings inside ``` code fences are ignored."""
    sections: List[Tuple[str, str]] = []
    stack: List[Tuple[int, str]] = []  # (level, heading) for levels 2+
    buffer: List[str] = []
    in_fence = False

    def flush():
        body = "\n".join(buffer).strip()
        if body:
            path = " > ".join(h for _, h in stack) or "Introduction"
            sections.append((path, body))
        buffer.clear()

    for line in doc.text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if not match:
            buffer.append(line)
            continue
        level, heading = len(match.group(1)), match.group(2).strip()
        flush()
        if level == 1:          # the H1 is the document title; it resets the path
            stack.clear()
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
    flush()
    return sections


def _units(text: str, max_chars: int) -> List[str]:
    """Break text into pieces no longer than max_chars: paragraphs, then lines, then sentences."""
    units: List[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            units.append(para)
            continue
        pieces = para.split("\n") if "\n" in para else SENTENCE_END.split(para)
        for piece in pieces:
            piece = piece.strip()
            if len(piece) <= max_chars:
                units.append(piece)
            else:  # one enormous sentence: hard cut as a last resort
                units.extend(piece[i:i + max_chars] for i in range(0, len(piece), max_chars))
    return units


def split_long(text: str, max_chars: int, overlap: int) -> List[str]:
    """Greedily pack units into pieces up to max_chars, repeating the tail of each piece in the next."""
    if len(text) <= max_chars:
        return [text]
    pieces: List[str] = []
    current: List[str] = []
    size = 0
    for unit in _units(text, max_chars):
        if current and size + len(unit) + 2 > max_chars:
            pieces.append("\n\n".join(current))
            # Overlap: keep trailing units that fit within `overlap` characters.
            carried: List[str] = []
            carried_size = 0
            for prev in reversed(current):
                if carried_size + len(prev) > overlap:
                    break
                carried.insert(0, prev)
                carried_size += len(prev) + 2
            current, size = carried, carried_size
        current.append(unit)
        size += len(unit) + 2
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def chunk_document(doc: Document, max_chars: int = 1200, overlap: int = 150) -> List[Chunk]:
    chunks: List[Chunk] = []
    for section, body in split_sections(doc):
        for piece in split_long(body, max_chars, overlap):
            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}#{len(chunks)}",
                doc_id=doc.doc_id,
                title=doc.title,
                section=section,
                category=doc.category,
                text=piece,
                source=doc.source,
            ))
    return chunks


def chunk_documents(docs: List[Document], max_chars: int = 1200, overlap: int = 150) -> List[Chunk]:
    return [chunk for doc in docs for chunk in chunk_document(doc, max_chars, overlap)]
