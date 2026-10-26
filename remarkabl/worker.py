import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path

from .cloud import Cloud
from .config import Settings
from .documents import extract_document
from .errors import NotConfigured, PermanentError
from .llm import Oracle
from .search import Search
from .storage import Store
from .telegram import Telegram

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.oracle = Oracle(settings)
        self.search = Search(store, settings)
        self.cloud = Cloud(settings)
        self.telegram = Telegram(settings, store)

    async def run_turn(self, turn_id):
        with self.store.connect() as db:
            turn = dict(db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone())
            if turn["status"] == "complete":
                return
            db.execute(
                "UPDATE turns SET status='processing',error=NULL,updated=? WHERE id=?",
                (time.time(), turn_id),
            )
        # Persist OCR separately so retrying an answer does not repeat OCR charges.
        question = turn["transcription"]
        if not question:
            image = Path(turn["image_path"]).read_bytes()
            if turn["page_capture"]:
                question, context = await self.oracle.transcribe_page(image)
                turn["page_context"] = context
            else:
                question = await self.oracle.transcribe(image)
            with self.store.connect() as db:
                db.execute("UPDATE turns SET transcription=?,page_context=? WHERE id=?",
                           (question, turn["page_context"], turn_id))
        sources = await asyncio.to_thread(self.search.retrieve, question)
        if turn["page_context"]:
            sources.insert(0, {"title": "PDF на экране", "label": "видимый фрагмент страницы",
                               "text": turn["page_context"], "document_id": "screen", "origin": None})
            sources = sources[:6]
        history = self.store.history(turn["conversation_id"], turn["created"])
        last_delta = 0.0

        async def progress(text):
            nonlocal last_delta
            now = time.monotonic()
            if now - last_delta < 0.15:
                return
            last_delta = now
            with self.store.connect() as db:
                db.execute("UPDATE turns SET answer=?,updated=? WHERE id=?",
                           (text, time.time(), turn_id))

        answer, used = await self.oracle.answer(question, history, sources, on_delta=progress)
        citations = [
            {
                "number": n,
                "document_id": sources[n - 1]["document_id"],
                "title": sources[n - 1]["title"],
                "location": sources[n - 1]["label"],
                "url": sources[n - 1]["origin"],
            }
            for n in used
        ]
        with self.store.connect() as db:
            db.execute(
                "UPDATE turns SET status='complete',answer=?,citations=?,error=NULL,updated=? WHERE id=?",
                (answer, json.dumps(citations, ensure_ascii=False), time.time(), turn_id),
            )

    async def process(self, job):
        kind, target = job["kind"], job["target"]
        if kind == "turn":
            return await self.run_turn(target)
        if kind == "telegram":
            return await self.telegram.ingest(job["payload"])
        if kind == "notify":
            return await self.telegram.tell(job["payload"]["text"])
        with self.store.connect() as db:
            document = dict(db.execute("SELECT * FROM documents WHERE id=?", (target,)).fetchone())
        if kind == "index":
            if document["status"] == "indexed":
                return
            fragments = await asyncio.to_thread(extract_document, document, self.settings)
            if not fragments:
                raise PermanentError(
                    "В документе нет распознаваемого текста; он всё равно доступен для чтения."
                )
            await asyncio.to_thread(self.search.index, target, fragments)
            self.store.enqueue(
                f"notify:index:{target}",
                "notify",
                target,
                {"text": f"Можно задавать вопросы по материалу: {document['title']}."},
            )
        if kind == "publish":
            if document["cloud_status"] == "published":
                return
            cloud_id = document["cloud_id"] or await self.cloud.publish(document)
            if not cloud_id:
                raise PermanentError("Облако не вернуло идентификатор документа.")
            with self.store.connect() as db:
                db.execute("UPDATE documents SET cloud_id=? WHERE id=?", (cloud_id, target))
            document["cloud_id"] = cloud_id
            await self.cloud.rename(document)
            with self.store.connect() as db:
                db.execute(
                    "UPDATE documents SET cloud_status='published',cloud_id=?,error=NULL WHERE id=?",
                    (cloud_id, target),
                )
                self.store.enqueue(
                    f"notify:publish:{target}",
                    "notify",
                    target,
                    {
                        "text": f"Готово для планшета: {document['title']}. Подключите reMarkable к Wi-Fi для синхронизации."
                    },
                    db=db,
                )

    async def heartbeat(self, job_id):
        while True:
            await asyncio.sleep(30)
            self.store.renew(job_id)

    async def once(self, kinds):
        job = self.store.claim(kinds)
        if not job:
            return False
        heartbeat = asyncio.create_task(self.heartbeat(job["id"]))
        try:
            await self.process(job)
            self.store.finish(job["id"])
        except asyncio.CancelledError:
            self.store.defer(job, "Сервис перезапускается; задание сохранено.", 0, configured=False)
            raise
        except Exception as exc:
            known = isinstance(exc, (PermanentError, NotConfigured))
            message = str(exc) if known else "Временная ошибка обработки; задание сохранено."
            # The exception class is useful for diagnostics; URLs and tokens aren't.
            log.warning("Job %s failed (%s)", job["kind"], type(exc).__name__)
            configured = not isinstance(exc, NotConfigured)
            terminal = isinstance(exc, PermanentError) or (
                configured and job["attempts"] >= self.settings.max_attempts
            )
            delay = min(300, self.settings.retry_base_seconds * 2 ** min(job["attempts"] - 1, 5))
            self.store.defer(job, message, delay, terminal, configured)
            if terminal and job["kind"] != "notify":
                self.store.enqueue(
                    "notify:error:" + job["id"], "notify", job["target"], {"text": message}
                )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def loop(self, kinds):
        while True:
            if not await self.once(kinds):
                await asyncio.sleep(0.5)
