import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app_setting import AppSettings


SUPPORTED_EXTENSIONS = {".md", ".pdf", ".txt"}
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

# Chunking strategies (select with HELP_CHUNK_STRATEGY in .env; compare them with
# eval/rag/chunking_experiments.py):
#   fixed            - fixed-size character windows per page (CHUNK_SIZE / CHUNK_OVERLAP)
#   structure        - one chunk per Q&A / heading, repeated page headers removed
#   structure_header - as structure, with "document > section" prepended to each chunk
CHUNK_STRATEGIES = ("fixed", "structure", "structure_header")
CHUNK_STRATEGY = "fixed"
STRUCTURE_MAX_CHARS = 1500  # longer sections are sub-split with the fixed chunker

QA_MARKER = re.compile(r"(?m)^[ \t]*(?:Q\d+(?:\.\d+)*\b|#{1,6}[ \t])")
SECTION_HEADING = re.compile(r"^[ \t]*\d{1,2}\.[ \t]+[A-Z][^\n]{2,80}$")
PAGE_NUMBER_LINE = re.compile(r"^\s*page\s+\d+\s*$", re.I)


def _resolve_project_path(path: str) -> Path:
    configured_path = Path(path)
    if configured_path.is_absolute():
        return configured_path
    project_root = Path(__file__).resolve().parents[3]
    return project_root / configured_path


def create_help_vector_store(embeddings: Any, settings: AppSettings, check_model: bool = True) -> Chroma:
    """Open (or create) the help-document index. The embedding model that built the
    index is recorded with it; searching it with a different model would silently
    return wrong results (MiniLM and bge-small both produce 384 numbers), so a
    mismatch stops the app with instructions instead."""
    from clients.llm_client import embedding_model_id

    model_id = embedding_model_id(embeddings)
    persist_directory = _resolve_project_path(settings.HELP_CHROMA_DIR)
    vector_store = Chroma(
        collection_name="help_documents",
        embedding_function=embeddings,
        persist_directory=str(persist_directory),
        collection_metadata={"hnsw:space": "cosine", "embedding_model": model_id},
    )
    if check_model:
        built_with = (vector_store._collection.metadata or {}).get("embedding_model")
        if built_with and built_with != model_id:
            raise ValueError(
                f"The help-document index was built with '{built_with}', but EMBEDDING_PROVIDER now "
                f"gives '{model_id}'. Rebuild the index: cd src; python -m utils.embeddings.help_document_store"
            )
    return vector_store


def _split_text(text: str) -> list[str]:
    text = text.strip()
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _strip_repeated_lines(pages: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    """Remove page headers/footers: lines repeated on most pages, and 'Page N' lines."""
    if len(pages) < 3:
        return pages
    counts: Counter = Counter()
    for _, text in pages:
        counts.update({line.strip() for line in text.splitlines() if line.strip()})
    threshold = max(3, int(0.6 * len(pages) + 0.5))
    boilerplate = {line for line, n in counts.items() if n >= threshold}
    return [(page, "\n".join(line for line in text.splitlines()
                               if line.strip() not in boilerplate and not PAGE_NUMBER_LINE.match(line)))
            for page, text in pages]


def _structure_chunks(pages: list[tuple[int | None, str]], title: str,
                      with_header: bool) -> list[tuple[int | None, str, str | None]]:
    """One chunk per Q&A (or markdown heading) across the whole document.
    Returns (page, text, section). Falls back to fixed chunks if no structure is found."""
    pages = _strip_repeated_lines(pages)
    full, page_starts = "", []
    for page, text in pages:
        page_starts.append((len(full), page))
        full += text.strip() + "\n"
    starts = [m.start() for m in QA_MARKER.finditer(full)]
    if not starts:
        return [(page, chunk, None) for page, text in pages for chunk in _split_text(text)]

    def page_at(pos: int):
        return next((p for s, p in reversed(page_starts) if s <= pos), None)

    bounds = ([0] if starts[0] > 0 else []) + starts + [len(full)]
    out: list[tuple[int | None, str, str | None]] = []
    section = None
    for a, b in zip(bounds, bounds[1:]):
        lines = [line for line in full[a:b].splitlines() if line.strip()]
        # section headings sit at the end of the previous block ("...answer.\n4. Fund transfers")
        trailing = []
        while lines and SECTION_HEADING.match(lines[-1]) and a in starts:
            trailing.insert(0, lines.pop().strip())
        if a not in starts:  # preamble: keep it, but its last heading opens the first section
            while lines and SECTION_HEADING.match(lines[-1]):
                trailing.insert(0, lines.pop().strip())
        body = "\n".join(lines).strip()
        if body:
            pieces = [body] if len(body) <= STRUCTURE_MAX_CHARS else _split_text(body)
            for piece in pieces:
                if with_header:
                    piece = f"{title} > {section}\n{piece}" if section else f"{title}\n{piece}"
                out.append((page_at(a), piece, section))
        if trailing:
            section = trailing[-1]
    return out


def _read_document_pages(path: Path) -> list[tuple[int | None, str]]:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return [
            (page_number, page.extract_text() or "")
            for page_number, page in enumerate(reader.pages, start=1)
        ]
    return [(None, path.read_text(encoding="utf-8"))]


def load_help_documents(documents_dir: str, strategy: str | None = None) -> tuple[list[Document], list[str]]:
    strategy = (strategy or CHUNK_STRATEGY).lower().strip()
    if strategy not in CHUNK_STRATEGIES:
        raise ValueError(f"Unknown chunk strategy {strategy!r}; use one of {CHUNK_STRATEGIES}")
    source_directory = _resolve_project_path(documents_dir)
    if not source_directory.is_dir():
        raise FileNotFoundError(
            f"Help documents directory does not exist: {source_directory}"
        )

    documents: list[Document] = []
    source_files = sorted(
        path for path in source_directory.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    for path in source_files:
        relative_source = path.relative_to(source_directory).as_posix()
        if strategy != "fixed":
            chunks = _structure_chunks(_read_document_pages(path), path.stem,
                                       with_header=(strategy == "structure_header"))
            for chunk_number, (page_number, chunk, section) in enumerate(chunks):
                metadata: dict[str, Any] = {"source": relative_source, "chunk": chunk_number,
                                            "chunk_strategy": strategy}
                if page_number is not None:
                    metadata["page"] = page_number
                if section:
                    metadata["section"] = section
                documents.append(Document(page_content=chunk, metadata=metadata))
            continue
        for page_number, text in _read_document_pages(path):
            for chunk_number, chunk in enumerate(_split_text(text)):
                metadata: dict[str, Any] = {
                    "source": relative_source,
                    "chunk": chunk_number,
                }
                if page_number is not None:
                    metadata["page"] = page_number
                documents.append(Document(page_content=chunk, metadata=metadata))

    return documents, [path.name for path in source_files]


def ingest_help_documents(settings: AppSettings, embeddings: Any) -> int:
    documents, _source_files = load_help_documents(settings.HELP_DOCUMENTS_DIR, settings.HELP_CHUNK_STRATEGY)
    if not documents:
        raise ValueError(
            "No extractable text found in the configured help documents directory."
        )

    vector_store = create_help_vector_store(embeddings, settings, check_model=False)
    vector_store.delete_collection()
    vector_store = create_help_vector_store(embeddings, settings, check_model=False)
    document_ids = [
        hashlib.sha256(
            f"{document.metadata['source']}:{document.metadata.get('page', '')}:"
            f"{document.metadata['chunk']}:{document.page_content}".encode("utf-8")
        ).hexdigest()
        for document in documents
    ]
    vector_store.add_documents(documents, ids=document_ids)
    return len(documents)


def main() -> None:
    from clients.llm_client import get_embeddings

    settings = AppSettings()
    embeddings = get_embeddings(settings.EMBEDDING_PROVIDER)
    count = ingest_help_documents(settings, embeddings)
    print(
        f"Indexed {count} chunks from {settings.HELP_DOCUMENTS_DIR} "
        f"using {settings.EMBEDDING_PROVIDER} embeddings."
    )


if __name__ == "__main__":
    main()
