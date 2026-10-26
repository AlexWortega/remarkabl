import asyncio
import base64
import binascii
import contextlib
import hashlib
import hmac
import io
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from .config import Settings
from .storage import Store
from .worker import Worker


class TurnInput(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    conversation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    image_base64: str
    strokes: list = Field(default_factory=list, max_length=10000)
    page_capture: bool = False


class BodyLimit:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope.get("headers", [])).get(b"content-length")
        if length and int(length) > self.limit:
            return await JSONResponse({"detail": "Запрос слишком большой."}, 413)(
                scope, receive, send
            )
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.limit:
                raise HTTPException(413, "Запрос слишком большой.")
            return message

        await self.app(scope, limited_receive, send)


def normalize_png(encoded, limit):
    if encoded.startswith("data:image/png;base64,"):
        encoded = encoded.split(",", 1)[1]
    if len(encoded) > (limit * 4 // 3 + 4):
        raise HTTPException(413, "Изображение слишком большое.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > limit:
            raise HTTPException(413, "Изображение слишком большое.")
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or image.width * image.height > 4_000_000:
                raise HTTPException(422, "Нужен PNG не больше 4 миллионов пикселей.")
            image.load()
            # Composite alpha onto white: transparent black must not become a blank page.
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            white.alpha_composite(rgba)
            output = io.BytesIO()
            white.convert("L").save(output, format="PNG")
            return output.getvalue()
    except (
        binascii.Error,
        ValueError,
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
    ) as exc:
        raise HTTPException(422, "Не удалось прочитать PNG.") from exc


def create_app(settings=None):
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(settings.data_dir, 0o700)
    store = Store(settings.data_dir / "remarkabl.sqlite3")
    worker = Worker(store, settings)

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        if settings.run_workers:
            tasks = [
                asyncio.create_task(worker.loop(("turn",))),
                asyncio.create_task(worker.loop(("telegram", "publish", "notify"))),
                asyncio.create_task(worker.loop(("index",))),
                asyncio.create_task(worker.telegram.supervise()),
            ]
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="remarkabl",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BodyLimit, limit=settings.max_body_bytes)
    app.state.store, app.state.worker, app.state.settings = store, worker, settings

    def authenticate(authorization: Annotated[str | None, Header()] = None):
        expected = "Bearer " + settings.device_token
        if (
            not settings.device_token
            or not authorization
            or not hmac.compare_digest(authorization.encode(), expected.encode())
        ):
            raise HTTPException(401, "Нужен ключ доступа к дневнику.")

    protected = [Depends(authenticate)]

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "remarkabl"}

    @app.get("/v1/status", dependencies=protected)
    def status():
        return {
            "llm_configured": bool(settings.openrouter_api_key or settings.mock_llm),
            "telegram_configured": bool(settings.telegram_bot_token),
            "large_telegram_files": bool(settings.telegram_api_id and settings.telegram_api_hash),
            "cloud_configured": bool(settings.rmfakecloud_url),
            "mock_llm": settings.mock_llm,
            "documents": len(store.documents()),
        }

    @app.post("/v1/turns", status_code=202, dependencies=protected)
    def submit(turn: TurnInput):
        png = normalize_png(turn.image_base64, settings.max_image_bytes)
        fingerprint = hashlib.sha256(
            png
            + turn.conversation_id.encode()
            + json.dumps(turn.strokes, separators=(",", ":")).encode()
            + (b":pdf" if turn.page_capture else b"")
        ).hexdigest()
        directory = settings.data_dir / "turns"
        directory.mkdir(exist_ok=True)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT input_hash FROM turns WHERE id=?", (turn.id,)).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise HTTPException(409, "Этот ID уже принадлежит другому вопросу.")
            else:
                path = directory / f"{turn.id}.png"
                tmp = path.with_suffix(".tmp")
                with tmp.open("wb") as file:
                    file.write(png)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(tmp, path)
                (directory / f"{turn.id}.strokes.json").write_text(json.dumps(turn.strokes))
                now = time.time()
                db.execute(
                    "INSERT INTO turns(id,conversation_id,input_hash,image_path,created,updated) VALUES(?,?,?,?,?,?)",
                    (turn.id, turn.conversation_id, fingerprint, str(path), now, now),
                )
                db.execute("UPDATE turns SET page_capture=? WHERE id=?", (turn.page_capture, turn.id))
                store.enqueue("turn:" + turn.id, "turn", turn.id, db=db)
        return store.turn(turn.id)

    @app.get("/v1/turns/{turn_id}", dependencies=protected)
    def result(turn_id: str):
        turn = store.turn(turn_id)
        if not turn:
            raise HTTPException(404, "Вопрос не найден.")
        return turn

    @app.post("/v1/turns/{turn_id}/retry", dependencies=protected)
    def retry(turn_id: str):
        with store.connect() as db:
            row = db.execute("SELECT status FROM turns WHERE id=?", (turn_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Вопрос не найден.")
            if row[0] == "error":
                db.execute("UPDATE turns SET status='queued',error=NULL WHERE id=?", (turn_id,))
                db.execute(
                    "UPDATE jobs SET status='queued',attempts=0,due=0,error=NULL WHERE id=?",
                    ("turn:" + turn_id,),
                )
        return store.turn(turn_id)

    @app.get("/v1/turns/{turn_id}/events", dependencies=protected)
    async def events(turn_id: str):
        if not store.turn(turn_id):
            raise HTTPException(404, "Вопрос не найден.")

        async def snapshots():
            previous, last_heartbeat = None, time.monotonic()
            while True:
                value = store.turn(turn_id)
                payload = json.dumps(value, ensure_ascii=False)
                if payload != previous:
                    previous = payload
                    yield f"data: {payload}\n\n"
                if value["status"] in ("complete", "error"):
                    return
                if time.monotonic() - last_heartbeat > 10:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(0.15)

        return StreamingResponse(snapshots(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/v1/library", dependencies=protected)
    def library():
        return store.documents()

    @app.get("/simulator")
    def simulator():
        return FileResponse(Path(__file__).parent / "static" / "simulator.html")

    @app.get("/fonts/caveat")
    def font():
        return FileResponse(Path("assets/Caveat.ttf"), media_type="font/ttf")

    return app
