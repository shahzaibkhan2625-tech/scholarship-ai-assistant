"""RAG document loaders — turn raw fetched content into loadable text with
provenance metadata, ready for chunking (rag/chunk.py)."""

from dataclasses import dataclass

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class LoadedDocument:
    text: str
    source_url: str
    scholarship_id: str


def load_html(html: str, *, source_url: str, scholarship_id: str) -> LoadedDocument:
    """Strip markup/scripts/styles down to visible text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return LoadedDocument(text=text, source_url=source_url, scholarship_id=scholarship_id)


def load_plain_text(text: str, *, source_url: str, scholarship_id: str) -> LoadedDocument:
    return LoadedDocument(text=" ".join(text.split()), source_url=source_url, scholarship_id=scholarship_id)
