import asyncio

import fitz
import pytest
from ebooklib import epub

from remarkabl.documents import (
    accept_document,
    chunks,
    extract_document,
    make_article_pdf,
    pdf_fragments,
)
from remarkabl.errors import PermanentError, RetryableError
from remarkabl.net import PublicNetwork, validate_url
from remarkabl.telegram import authorized


@pytest.mark.asyncio
async def test_pdf_duplicate_and_search_pipeline(app, settings, tmp_path):
    path = tmp_path / "book.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((30, 50), "Memory is the ability to store and recall information.")
        pdf.save(path)
    store, worker = app.state.store, app.state.worker
    a = accept_document(store, settings, path, "Memory.pdf")
    b = accept_document(store, settings, path, "Same book.pdf")
    assert a == b and len(store.documents()) == 1
    assert await worker.once(("index",))
    found = worker.search.retrieve("memory recall")
    assert found[0]["title"] == "Memory"
    assert found[0]["label"] == "стр. 1"
    assert "store" in found[0]["text"]
    assert store.documents()[0]["status"] == "indexed"
    # Cloud configuration missing: retain the independent publication job.
    await worker.once(("publish",))
    assert store.documents()[0]["cloud_status"] == "waiting"


def test_epub_chapter_locations(app, settings, tmp_path):
    path = tmp_path / "book.epub"
    book = epub.EpubBook()
    book.set_identifier("example")
    book.set_title("Example")
    book.set_language("ru")
    chapter = epub.EpubHtml(title="Память", file_name="memory.xhtml", lang="ru")
    chapter.content = "<h1>Память</h1><p>Память сохраняет знания о прочитанных книгах.</p>"
    book.add_item(chapter)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.toc = [chapter]
    book.spine = [chapter]
    epub.write_epub(str(path), book)
    doc_id = accept_document(app.state.store, settings, path, "Книга.epub")
    with app.state.store.connect() as db:
        document = dict(db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())
    fragments = extract_document(document, settings)
    assert fragments[0].label == "Память"
    assert "знания" in fragments[0].text


def test_only_private_allowed_sender_can_ingest(app, settings):
    allowed = {
        "chat": {"type": "private", "id": 241154130},
        "from": {"id": 241154130},
        "text": "/library",
    }
    assert authorized(allowed, settings)
    assert not authorized({**allowed, "from": {"id": 12}}, settings)
    assert not authorized({**allowed, "chat": {"type": "group", "id": -1}}, settings)
    app.state.worker.telegram.save_updates(
        [
            {"update_id": 40, "message": {**allowed, "from": {"id": 12}}},
            {"update_id": 41, "message": allowed},
            {"update_id": 41, "message": allowed},
        ]
    )
    assert app.state.store.state("telegram_offset") == "42"
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='telegram'").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_ssrf_private_dns_is_blocked_at_connection_time(monkeypatch):
    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 80))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(PermanentError, match="внутренний"):
        await PublicNetwork().connect_tcp("rebinding.example", 80)
    for url in (
        "file:///etc/passwd",
        "https://user:pass@site.example",
        "https://host.example:8888",
    ):
        with pytest.raises(PermanentError):
            validate_url(url)


def test_chunking_terminates_and_preserves_long_words():
    text = "я" * 3500
    pieces = list(chunks(text))
    assert len(pieces) == 3
    assert all(0 < len(piece) <= 1600 for piece in pieces)


def test_article_pdf_preserves_cyrillic_text_and_page_location(settings, tmp_path):
    path = tmp_path / "article.pdf"
    make_article_pdf(
        "Память и книги",
        "Память сохраняет информацию. Чтение помогает запоминать новые знания.",
        "https://example.org/article",
        path,
        settings,
    )
    fragments = list(pdf_fragments(path, settings))
    assert fragments[0].label == "стр. 1"
    assert "Чтение помогает запоминать" in fragments[0].text
    assert "https://example.org/article" in fragments[0].text


@pytest.mark.asyncio
async def test_publication_retry_after_rename_failure_does_not_upload_again(
    app, settings, tmp_path, monkeypatch
):
    path = tmp_path / "book.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 50), "A sample document with readable text.")
        pdf.save(path)
    store, worker = app.state.store, app.state.worker
    doc_id = accept_document(store, settings, path, "Sample.pdf")
    uploads, renames = [], []

    async def publish(document):
        uploads.append(document["id"])
        return "durable-cloud-id"

    async def rename(document):
        renames.append(document["cloud_id"])
        if len(renames) == 1:
            raise RetryableError("temporary failure")

    monkeypatch.setattr(worker.cloud, "publish", publish)
    monkeypatch.setattr(worker.cloud, "rename", rename)
    await worker.once(("publish",))
    with store.connect() as db:
        row = dict(db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())
        assert row["cloud_id"] == "durable-cloud-id"
        assert row["cloud_status"] != "published"
    await worker.once(("publish",))
    assert uploads == [doc_id]
    assert renames == ["durable-cloud-id", "durable-cloud-id"]
    assert store.documents()[0]["cloud_status"] == "published"


@pytest.mark.asyncio
async def test_cloud_retry_uses_stable_filename_and_private_header(
    app, settings, tmp_path, monkeypatch
):
    import httpx

    from remarkabl.cloud import Cloud

    path = tmp_path / "sample.pdf"
    path.write_bytes(b"sample")
    settings.rmfakecloud_url = "http://cloud.test"
    settings.rmfakecloud_user = "owner"
    settings.rmfakecloud_password = "password"
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("login"):
            return httpx.Response(
                200,
                text="test-cloud-token",
                headers={"set-cookie": "AuthToken=test-cloud-token; Secure"},
            )
        assert request.headers["Authorization"] == "Bearer test-cloud-token"
        assert b"[aaaaaaaaaaaa].pdf" in request.read()
        return httpx.Response(409, json={"docId": "existing-document"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await Cloud(settings).publish(
        {"title": "Book", "id": "a" * 64, "reading_path": str(path)}
    )
    assert result == "existing-document"
    assert len(requests) == 2
