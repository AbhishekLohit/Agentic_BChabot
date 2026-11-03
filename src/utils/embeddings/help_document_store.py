import hashlib
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app_setting import AppSettings


SUPPORTED_EXTENSIONS = {".md", ".pdf", ".txt"}
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200


def _resolve_project_path(path: str) -> Path:
    configured_path = Path(path)
    if configured_path.is_absolute():
        return configured_path
    project_root = Path(__file__).resolve().parents[3]
    return project_root / configured_path


def create_help_vector_store(embeddings: Any, settings: AppSettings) -> Chroma:
    persist_directory = _resolve_project_path(settings.HELP_CHROMA_DIR)
    return Chroma(
        collection_name="help_documents",
        embedding_function=embeddings,
        persist_directory=str(persist_directory),
    )


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


def _read_document_pages(path: Path) -> list[tuple[int | None, str]]:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return [
            (page_number, page.extract_text() or "")
            for page_number, page in enumerate(reader.pages, start=1)
        ]
    return [(None, path.read_text(encoding="utf-8"))]


def load_help_documents(documents_dir: str) -> tuple[list[Document], list[str]]:
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
    documents, _source_files = load_help_documents(settings.HELP_DOCUMENTS_DIR)
    if not documents:
        raise ValueError(
            "No extractable text found in the configured help documents directory."
        )

    vector_store = create_help_vector_store(embeddings, settings)
    vector_store.delete_collection()
    vector_store = create_help_vector_store(embeddings, settings)
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
    from clients.llm_client import get_openai_embeddings

    settings = AppSettings()
    embeddings = get_openai_embeddings(model=settings.EMBED_MODEL)
    count = ingest_help_documents(settings, embeddings)
    print(f"Indexed {count} chunks from {settings.HELP_DOCUMENTS_DIR}.")


if __name__ == "__main__":
    main()
