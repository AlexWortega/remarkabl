import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import ebooklib
import pymupdf as fitz
import trafilatura
from bs4 import BeautifulSoup
from ebooklib import epub
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .config import Settings
from .errors import PermanentError
from .storage import Store


@dataclass
class Fragment:
    label: str
    text: str


def chunks(text: str, size=1600, overlap=180):
    text = re.sub(r"[ \t]+", " ", text).strip()
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            split = text.rfind(" ", start + size // 2, end)
            if split > start:
                end = split
        piece = text[start:end].strip()
        if piece:
            yield piece
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def pdf_fragments(path: Path, settings: Settings):
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise PermanentError("Не удалось открыть PDF.") from exc
    with doc:
        if doc.needs_pass:
            raise PermanentError("PDF защищён паролем. Пришлите незашифрованную копию.")
        if len(doc) > settings.max_pdf_pages:
            raise PermanentError(f"PDF содержит больше {settings.max_pdf_pages} страниц.")
        for index, page in enumerate(doc):
            text = page.get_text("text").strip()
            if len(text) < 20 and page.get_images():
                if shutil.which("tesseract") is None:
                    raise PermanentError("Для сканов требуется Tesseract с языками rus и eng.")
                with tempfile.TemporaryDirectory() as tmp:
                    png_path = Path(tmp) / "page.png"
                    # Bound raster memory even for PDFs with pathological page dimensions.
                    scale = min(150 / 72, 2400 / max(page.rect.width, page.rect.height))
                    page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(png_path)
                    try:
                        result = subprocess.run(
                            ["tesseract", png_path.name, "stdout", "-l", "rus+eng"],
                            cwd=tmp,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=120,
                            check=True,
                        )
                    except (subprocess.SubprocessError, OSError) as exc:
                        raise PermanentError(
                            f"Не удалось распознать страницу {index + 1}."
                        ) from exc
                    text = result.stdout.strip()
            for piece in chunks(text):
                yield Fragment(f"стр. {index + 1}", piece)


def epub_fragments(path: Path):
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 300 * 1024 * 1024:
                raise PermanentError("Распакованный EPUB слишком большой.")
            if "META-INF/encryption.xml" in archive.namelist():
                encrypted = ElementTree.fromstring(archive.read("META-INF/encryption.xml"))
                # Font obfuscation is common in DRM-free EPUBs; text encryption isn't.
                for item in encrypted.iter():
                    if item.tag.endswith("CipherReference") and item.attrib.get(
                        "URI", ""
                    ).lower().endswith((".html", ".xhtml", ".xml")):
                        raise PermanentError("Текст EPUB зашифрован. Пришлите копию без DRM.")
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
        by_id = {item.id: item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
        ordered = [by_id[item_id] for item_id, _ in book.spine if item_id in by_id]
        if not ordered:
            ordered = list(by_id.values())
        for index, item in enumerate(ordered):
            soup = BeautifulSoup(item.get_content(), "html.parser")
            heading = soup.find(["h1", "h2", "title"])
            label = heading.get_text(" ", strip=True) if heading else f"глава {index + 1}"
            for tag in soup(["script", "style"]):
                tag.decompose()
            for piece in chunks(soup.get_text(" ", strip=True)):
                yield Fragment(label[:200], piece)
    except PermanentError:
        raise
    except Exception as exc:
        raise PermanentError("Не удалось прочитать EPUB.") from exc


def make_article_pdf(title, text, origin, output: Path, settings: Settings):
    candidates = [
        settings.document_font,
        Path("assets/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    font = next((path for path in candidates if path and path.is_file()), None)
    if font is None:
        raise PermanentError("Не установлен шрифт для создания PDF.")
    if "ReadingFont" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ReadingFont", str(font)))
    style = ParagraphStyle(
        "Reading",
        fontName="ReadingFont",
        fontSize=15,
        leading=23,
        spaceAfter=12,
        splitLongWords=True,
    )
    heading = ParagraphStyle("Title", parent=style, fontSize=23, leading=31)
    items = [Paragraph(escape(title), heading), Paragraph(escape(origin), style), Spacer(1, 20)]
    for para in text.splitlines():
        if para.strip():
            items.append(Paragraph(escape(para), style))
    SimpleDocTemplate(
        str(output),
        pagesize=(702, 936),
        topMargin=45,
        bottomMargin=45,
        leftMargin=45,
        rightMargin=45,
        title=title,
    ).build(items)


def accept_document(
    store: Store, settings: Settings, incoming: Path, filename: str, origin=None, content_type=""
) -> str:
    if incoming.stat().st_size > settings.max_document_bytes:
        raise PermanentError(f"Лимит файла — {settings.max_document_mb} МБ.")
    with incoming.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    with store.connect() as db:
        existing = db.execute("SELECT id FROM documents WHERE id=?", (digest,)).fetchone()
    if existing:
        return digest
    document_dir = settings.data_dir / "documents" / digest
    document_dir.mkdir(parents=True, exist_ok=True)
    with incoming.open("rb") as file:
        magic = file.read(8)
    title = Path(filename).stem[:180] or "Документ"
    if magic.startswith(b"%PDF-"):
        kind = "pdf"
    elif magic.startswith(b"PK") and filename.lower().endswith(".epub"):
        kind = "epub"
    elif origin and ("html" in content_type or b"<" in magic):
        kind = "article"
    else:
        raise PermanentError("Поддерживаются PDF, EPUB и ссылки на обычные веб-статьи.")
    path = document_dir / f"original.{ {'article': 'html'}.get(kind, kind) }"
    temp = path.with_suffix(".tmp")
    shutil.copyfile(incoming, temp)
    os.replace(temp, path)
    reading = path
    if kind == "article":
        raw = path.read_text(errors="replace")
        metadata = trafilatura.extract_metadata(raw, default_url=origin)
        title = (metadata.title if metadata and metadata.title else title)[:180]
        text = trafilatura.extract(raw, include_comments=False, include_tables=True)
        if not text or len(text) < 80:
            raise PermanentError("Не удалось извлечь статью. Пришлите PDF или доступную ссылку.")
        (document_dir / "article.txt").write_text(text)
        reading = document_dir / "article.pdf"
        make_article_pdf(title, text, origin, reading, settings)
    with store.connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO documents(id,title,kind,path,reading_path,origin,created) VALUES(?,?,?,?,?,?,?)",
            (digest, title, kind, str(path), str(reading), origin, time.time()),
        )
        store.enqueue(f"index:{digest}", "index", digest, db=db)
        # Publish independently: failures in OCR must not prevent reading the original.
        store.enqueue(f"publish:{digest}", "publish", digest, db=db)
    return digest


def extract_document(document, settings):
    path = Path(document["path"])
    if document["kind"] == "pdf":
        return list(pdf_fragments(path, settings))
    if document["kind"] == "epub":
        return list(epub_fragments(path))
    text = (path.parent / "article.txt").read_text()
    return [Fragment("статья", text) for text in chunks(text)]
