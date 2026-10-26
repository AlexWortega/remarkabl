import asyncio
import contextlib
import logging
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from .config import Settings
from .documents import accept_document
from .errors import PermanentError, RetryableError
from .net import download_public
from .storage import Store

log = logging.getLogger(__name__)


def authorized(message: dict, settings: Settings):
    return (
        message.get("chat", {}).get("type") == "private"
        and message.get("from", {}).get("id") == settings.telegram_allowed_user_id
        and message.get("chat", {}).get("id") == settings.telegram_allowed_user_id
    )


class Telegram:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store

    async def api(self, method, **data):
        url = f"{self.settings.telegram_base}/bot{self.settings.telegram_bot_token}/{method}"
        async with httpx.AsyncClient(timeout=75) as client:
            try:
                response = await client.post(url, json=data)
            except httpx.HTTPError as exc:
                raise RetryableError("Временно нет связи с Telegram.") from exc
        # Never include the request URL or Telegram's raw payload in errors/logs.
        if response.status_code in (401, 403):
            raise PermanentError("Telegram отклонил токен бота.")
        if response.status_code >= 500 or response.status_code == 429:
            raise RetryableError("Telegram временно занят.")
        if response.status_code >= 400:
            raise PermanentError("Telegram отклонил запрос к боту.")
        result = response.json()
        if not result.get("ok"):
            raise RetryableError("Telegram не подтвердил операцию.")
        return result["result"]

    async def tell(self, text):
        if self.settings.telegram_bot_token:
            await self.api(
                "sendMessage",
                chat_id=self.settings.telegram_allowed_user_id,
                text=text[:4000],
                link_preview_options={"is_disabled": True},
            )

    def save_updates(self, updates):
        with self.store.connect() as db:
            for update in updates:
                message = update.get("message", {})
                if authorized(message, self.settings):
                    self.store.enqueue(
                        f"tg:{update['update_id']}",
                        "telegram",
                        str(update["update_id"]),
                        message,
                        db=db,
                    )
                self.store.set_state("telegram_offset", update["update_id"] + 1, db=db)

    async def poll(self):
        if not self.settings.telegram_bot_token:
            return
        # Switching from hosted Bot API is an explicit per-bot migration, not done every boot.
        local = bool(self.settings.telegram_api_id and self.settings.telegram_api_hash)
        if local and self.store.state("telegram_local_migrated") != "yes":
            old = Settings(
                **{**self.settings.model_dump(), "telegram_api_base": "https://api.telegram.org"},
                _env_file=None,
            )
            await Telegram(old, self.store).api("logOut")
            self.store.set_state("telegram_local_migrated", "yes")
        await self.api("deleteWebhook", drop_pending_updates=False)
        while True:
            try:
                updates = await self.api(
                    "getUpdates",
                    offset=int(self.store.state("telegram_offset", 0)),
                    timeout=50,
                    allowed_updates=["message"],
                )
                self.save_updates(updates)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Telegram polling paused (%s)", type(exc).__name__)
                await asyncio.sleep(15)

    async def ingest(self, message):
        if not authorized(message, self.settings):
            return
        text = message.get("text", "").strip()
        if text.startswith(("/start", "/help")):
            await self.tell(
                "Пришлите PDF, EPUB или ссылку на статью. Я сохраню материал и отправлю его на reMarkable.\n/library — библиотека\n/status — состояние загрузок"
            )
            return
        if text.startswith(("/library", "/status")):
            docs = self.store.documents()
            names = [
                f"{d['title']} — поиск: {d['status']}, планшет: {d['cloud_status']}"
                for d in docs[:25]
            ]
            await self.tell("\n".join(names) if names else "Библиотека пока пуста.")
            return
        if not message.get("document") and not text.startswith(("https://", "http://")):
            await self.tell("Пришлите документ PDF/EPUB или обычную ссылку на статью.")
            return
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.data_dir) as tmp:
            incoming = Path(tmp) / "incoming"
            origin, content_type = None, ""
            if doc := message.get("document"):
                size = doc.get("file_size", 0)
                if size > self.settings.max_document_bytes:
                    raise PermanentError(f"Лимит файла — {self.settings.max_document_mb} МБ.")
                if (
                    self.settings.telegram_base == "https://api.telegram.org"
                    and size > 20 * 1024 * 1024
                ):
                    raise PermanentError(
                        "Для вложений больше 20 МБ добавьте Telegram API ID/hash в Railway. Пока можно прислать прямую ссылку на PDF или EPUB."
                    )
                filename = doc.get("file_name", "document.pdf")
                if Path(filename).suffix.lower() not in (".pdf", ".epub"):
                    raise PermanentError("Поддерживаются файлы PDF и EPUB.")
                info = await self.api("getFile", file_id=doc["file_id"])
                file_path = info["file_path"]
                if Path(file_path).is_absolute():
                    local_path = Path(file_path).resolve()
                    if not local_path.is_relative_to(self.settings.telegram_dir.resolve()):
                        raise PermanentError("Bot API вернул путь вне разрешённого хранилища.")
                    # Stream the copy: large books should not occupy their size in RAM.
                    await asyncio.to_thread(self.copy_limited, local_path, incoming)
                else:
                    url = f"{self.settings.telegram_base}/file/bot{self.settings.telegram_bot_token}/{file_path}"
                    await self.download_telegram(url, incoming)
            else:
                url = text.split()[0]
                filename = unquote(Path(urlsplit(url).path).name) or "article.html"
                content_type, origin = await download_public(
                    url, incoming, self.settings.max_document_bytes
                )
            document_id = await asyncio.to_thread(
                accept_document, self.store, self.settings, incoming, filename, origin, content_type
            )
            with self.store.connect() as db:
                title = db.execute(
                    "SELECT title FROM documents WHERE id=?", (document_id,)
                ).fetchone()[0]
            await self.tell(f"Сохранено: {title}. Готовлю поиск и синхронизацию с планшетом.")

    def copy_limited(self, source, target):
        with source.open("rb") as incoming, target.open("wb") as outgoing:
            total = 0
            while chunk := incoming.read(1024 * 1024):
                total += len(chunk)
                if total > self.settings.max_document_bytes:
                    raise PermanentError("Файл превышает лимит размера.")
                outgoing.write(chunk)

    async def download_telegram(self, url, path):
        async with httpx.AsyncClient(timeout=180) as client:
            try:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    total = 0
                    with path.open("wb") as file:
                        async for block in response.aiter_bytes():
                            total += len(block)
                            if total > self.settings.max_document_bytes:
                                raise PermanentError("Файл превышает лимит размера.")
                            file.write(block)
            except httpx.HTTPError as exc:
                raise RetryableError("Не удалось скачать вложение из Telegram.") from exc

    async def supervise(self):
        while True:
            try:
                await self.poll()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Telegram startup paused (%s)", type(exc).__name__)
                await asyncio.sleep(15)

    async def safe_tell(self, text):
        with contextlib.suppress(Exception):
            await self.tell(text)
