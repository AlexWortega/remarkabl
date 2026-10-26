# Living Diary for reMarkable

Write with the pen, pause for five seconds, and watch the reply appear as handwritten strokes. The diary queues questions offline, sends them through your own server, and keeps a local history. A personal Telegram bot accepts books and articles, which sync to the standard reMarkable library through rmfakecloud.

**[Instructions: usage, setup, updates, and recovery](INSTRUCTIONS.md).**

The tablet integration targets **reMarkable 2, firmware 3.20.0.92, ARMv7**. Other models and firmware versions require compatibility work. The server is independent of the tablet model. This repository contains an application and interface extensions, rather than a replacement firmware image.

## Current status

The native diary has completed a handwritten question and displayed a reply on the tablet. Server-side streaming and PDF page processing have also been tested.

The PDF overlay previously crashed the tablet because `grabToImage` requested an unsupported graphics layer. A separate Rust process now reads the existing framebuffer without asking Qt to render the page again. On-device measurements on October 13, 2026: **0.29 seconds, approximately 9 MB peak memory**. The replacement is installed and screen capture is verified; the full PDF button-to-answer interaction still needs confirmation on the tablet after this fix.

## Personal deployment

- [Railway project](https://railway.com/project/045103b6-07cd-4c46-a902-22a087215bd0)
- [Diary API](https://diary-production-763d.up.railway.app)
- [Personal cloud](https://cloud-production-b67a.up.railway.app)
- [Telegram bot](https://t.me/anototherbot)
- [Browser simulator](https://diary-production-763d.up.railway.app/simulator)

These are the existing owner's deployment addresses. For your own installation, configure your own credentials, bot, allowed Telegram user ID, and service URLs.

The bot accepts private messages only from the configured Telegram user (`241154130` in this deployment). Send a PDF, EPUB, or article URL. `/library` lists documents and their processing state; `/status` reports the document queue. Send restricted articles as files.

The application limit is 200 MB per document. Telegram attachments over 20 MB require `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in the `diary` service's Railway Variables. Obtain them through [Telegram API development tools](https://my.telegram.org). With those values configured, the container starts a local Bot API automatically; it listens only on `127.0.0.1:8081`. Before that setup, use a direct download link for larger documents.

## Questions on PDF pages

Open a PDF in the standard reader, write `AI: explain this paragraph`, and tap **LLM** in the bottom-right corner. The side panel is designed to show a streaming reply based on your handwritten question and the visible printed text. Tap LLM without a question to request an explanation of the visible passage. Close the panel with ×. Replies are saved in the diary history.

A screenshot is submitted only when you tap LLM. The overlay also attaches to ordinary notebook pages. Native pen handling remains in the standard application; `tablet/pdf-panel.qmd` loads `tablet/pdf-panel.qml` into the document view.

Screen capture checks the device, firmware, memory mapping, and pixel format. Unsupported configurations return an error. `remarkabl-diary --snapshot /tmp/page.png` saves a local screenshot without submitting it to a model.

The diary uses monochrome refresh, batches pen updates, saves drafts on a separate thread, and wakes the sync queue immediately after a question is saved. OpenRouter output reaches the tablet through SSE; completed lines are drawn before the whole answer finishes. Time to first text also depends on handwriting recognition and the model.

## Architecture and privacy

- `remarkabl/`: FastAPI, SQLite/WAL job queue, Telegram bot, OCR, retrieval, and document publishing.
- `tablet/`: ARMv7 Rust application with QTFB, handwriting, Cyrillic shaping, animated replies, history, and a persistent queue.
- `scripts/`: builds, SSH pairing, device updates, and staged Railway uploads.
- `deploy/`: containers, service configuration, and checksummed upstream downloads.
- `.railway/railway.ts`: services, health checks, and volumes; secret variables remain outside IaC management.

The external model receives the question image, up to six preceding conversation turns, and retrieved library excerpts. Complete books stay on your server. Retrieval uses the local `intfloat/multilingual-e5-small` model; scanned documents use Tesseract with `rus+eng`. Citations refer to PDF pages, EPUB chapters, or article URLs. Notebook pages are submitted only through an explicit diary question or LLM button action.

OpenRouter and Telegram credentials belong in Railway Variables. The tablet stores a separate device token. Local credentials and backups are excluded through `.gitignore`; `.env.example` contains empty credential fields. Cloning this repository does not recover the owner's secrets, notes, or server data.

The diary runs through AppLoad alongside the stock interface and cloud sync. Firmware 3.20.0.92 uses AppLoad v0.3.1; newer releases are not interchangeable with the tablet's Qt/QML version. The `remarkabl-xovi` service starts the extension at boot and keeps the stock interface after a failed startup attempt. After fixing the problem, rerun `scripts/update-device.sh`. Manual extension startup: `/home/root/xovi/start`. Firmware upgrades require another compatibility check and may require restoring the cloud proxy.

## Local quick start

```sh
uv sync --python 3.12 --group dev
cp .env.example .env
# Set DEVICE_TOKEN in .env.
# For test replies without paid API calls: MOCK_LLM=true.
# For retrieval without downloading a model: EMBEDDING_MODE=lexical.
uv run remarkabl --port 8000
```

Open http://127.0.0.1:8000/simulator and enter `DEVICE_TOKEN`. The simulator keeps queued questions in IndexedDB; the provider API key is never sent to the browser. `MOCK_LLM=true` generates explicit test replies without calling a model; it is disabled in the personal Railway deployment.

For semantic retrieval:

```sh
uv sync --extra semantic --group dev
```

The bundled Noto Sans font supports article-to-PDF conversion. The Docker image includes Tesseract and its language data. For local scan processing, install Tesseract plus the `rus` and `eng` languages separately. The aiogram Bot API 10.3 image is pinned by digest; its musl runtime is isolated from Python/Debian.

## API

All `/v1/*` endpoints require `Authorization: Bearer <DEVICE_TOKEN>`.

- `POST /v1/turns`: JSON `{id, conversation_id, image_base64, strokes?, page_capture?}`; returns `202` and job state. Images must be PNG, at most 8 MB and 4 million pixels. Strokes are arrays of lines containing `[x,y,radius]` points.
- `GET /v1/turns/{id}/events`: SSE with partial and final results.
- `GET /v1/turns/{id}`: `queued`, `processing`, `complete`, or `error`, with transcription, answer, and validated citations.
- `POST /v1/turns/{id}/retry`: retry a failed question using the same ID.
- `GET /v1/library`: documents, indexing state, and tablet publication state.
- `GET /v1/status`: integration readiness without exposing secret values.
- `GET /health`: unauthenticated process health check.

Repeating the same ID and content returns the existing result; different content under that ID returns `409`. OCR is persisted separately from the answer. Jobs survive restarts and temporary failures. Credential configuration errors do not consume retry attempts; temporary failures are retried up to six times. Document upload and search indexing have independent states.

## Build and update the tablet app

```sh
cargo install cargo-zigbuild --locked
rustup target add armv7-unknown-linux-musleabihf
sh scripts/build-tablet.sh
```

The output is a static ARMv7 binary in `dist/remarkabl/`, with embedded fonts and no dependency on the tablet's glibc. `scripts/update-device.sh` updates the application and services on an already configured tablet. `scripts/pair-device.sh` creates a dedicated SSH key; enter the device password locally when SSH prompts for it.

The `page_capture=true` request mode reads a handwritten question together with the visible printed passage. SQLite schema additions migrate automatically at startup.

Tablet configuration: `/home/root/remarkabl/device.env`. Queue and history: `/home/root/remarkabl/outbox/`. The `remarkabl-sync` service also sends questions while the diary is closed. If the open application owns the queue lock, the service retries after 15 seconds and resumes after the application closes. Questions are saved atomically before the handwriting is cleared; opening a new page preserves history.

Render a sample without a tablet:

```sh
cargo run --manifest-path tablet/Cargo.toml -- --render 'Hello. I am your living diary.' /tmp/diary.png
```

## Redeploy

```sh
python3 scripts/deploy-server.py
railway up deploy/cloud --path-as-root --service cloud --detach
```

The diary deploy script stages only server source and required assets. SSH keys, backups, `.env`, Rust builds, and local data stay out of the upload. Each service uses its own Railway volume mounted at `/data`. Keep the bot service running continuously and use one replica per SQLite database. Configuration fields are listed in `.env.example`; see the instructions for project linking and cloud credentials.

## Checks and recovery

```sh
uv run pytest -q
uv run ruff check remarkabl tests
cargo test --manifest-path tablet/Cargo.toml --locked
cargo clippy --manifest-path tablet/Cargo.toml --locked -- -D warnings
```

Python tests cover authentication, idempotency, restarts, lease recovery, provider failures, PDF/EPUB processing, retrieval, Telegram access control, SSRF, and cloud upload retries. Rust tests cover ARM32 protocol decoding, durable drafts and queueing, erasing, Cyrillic pagination, and framebuffer validation/orientation.

A pre-installation backup was saved locally as `.secrets/device-backup-3.20.0.92.tar.gz` (729 MB), with its file list in `.secrets/backup-file-list.txt`. Earlier system settings are also stored on the tablet as `/home/root/remarkabl/system-before.tar.gz`. These backups are outside Git and do not include notes created later.

Run `sh scripts/restore-stock.sh` to disable extension startup and restore the stock interface while keeping notes and cloud sync. Running `/home/root/xovi/stock` on the tablet restores the interface for the current session; rebooting alone may start XOVI again. Save a fresh backup before a firmware upgrade.

Third-party sources and licenses: [THIRD_PARTY.md](THIRD_PARTY.md).
