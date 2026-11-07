"""Step 1 of the pipeline: read company files from disk and clean them."""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SUPPORTED_EXTENSIONS = {".md", ".txt"}


@dataclass(frozen=True)
class Document:
    doc_id: str          # file name without extension, e.g. "refund-policy"
    title: str
    category: str        # general, billing, technical, account...
    updated: Optional[str]
    text: str            # cleaned body, frontmatter removed
    source: str          # file name, for citations and debugging


def parse_frontmatter(raw: str) -> Tuple[Dict[str, str], str]:
    """Split a `---`-fenced `key: value` header from the body. Missing header = empty dict."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    meta: Dict[str, str] = {}
    for line in raw[3:end].strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip()
    body = raw[end + 4:]
    return meta, body


def clean_text(text: str) -> str:
    """Normalise line endings and whitespace without touching the content."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _first_heading(text: str) -> Optional[str]:
    match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def load_document(path: Path) -> Optional[Document]:
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)
    body = clean_text(body)
    if not body:
        return None
    return Document(
        doc_id=path.stem,
        title=meta.get("title") or _first_heading(body) or path.stem.replace("-", " ").capitalize(),
        category=meta.get("category", "general").lower(),
        updated=meta.get("updated"),
        text=body,
        source=path.name,
    )


def load_documents(folder: Path) -> List[Document]:
    """Load every supported file in the folder (and subfolders), sorted for stable chunk IDs."""
    if not folder.is_dir():
        raise FileNotFoundError(f"Knowledge folder not found: {folder}")
    docs = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            doc = load_document(path)
            if doc:
                docs.append(doc)
    return docs
