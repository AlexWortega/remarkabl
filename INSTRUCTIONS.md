# Personal reMarkable instructions

Repository: [AlexWortega/remarkabl](https://github.com/AlexWortega/remarkabl). The configured tablet is a **reMarkable 2 running firmware 3.20.0.92**. The project provides an application and extensions to the stock interface. It does not include a replacement system firmware image.

## Everyday use

### Diary

1. Connect the tablet to Wi-Fi.
2. Open the side menu → Apps / AppLoad → **Живой дневник** (Living Diary, the installed app's Russian label).
3. Write a question with the pen and pause for about five seconds.
4. The reply appears as it is generated. The top controls let you start a new page, browse history, and turn answer pages.

Without a network connection, the question stays in the tablet's queue. The sync service sends it when the server becomes reachable, including while the diary is closed. Queue files and history are stored in `/home/root/remarkabl/outbox/`.

### Books and articles

1. Open [@anototherbot](https://t.me/anototherbot) and press Start.
2. Send a PDF, EPUB, or direct article URL. This deployment accepts only the owner's Telegram ID, `241154130`.
3. Use `/status` to check processing and `/library` to browse the library.
4. Connect the tablet to the network and wait for normal sync. Documents appear in the standard reMarkable library.

Telegram attachments over 20 MB require `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in the `diary` service. Until those are configured, send larger documents through direct download links. The application limit is 200 MB per document. Send restricted articles as files.

### Ask a question next to a PDF

1. Open a PDF in the standard reMarkable reader.
2. Write a question on the page, for example: `AI: explain this paragraph`.
3. Tap **LLM** in the bottom-right corner. The side panel should show an answer based on your question and the visible passage.
4. Close the panel with × to continue writing.

Only the current screen is submitted when you press the button. The complete book is not automatically attached to that request. The overlay also attaches to ordinary notebook pages.

**Status as of October 13, 2026:** the replacement screen capture is installed and avoids the crashing `grabToImage` call. Capture alone was verified on the tablet at 0.29 seconds and approximately 9 MB memory. Server-side page processing and streaming were also tested. The complete “tap LLM in a PDF → see the answer in the panel” flow still needs on-device confirmation after the fix.

## Services and configuration

These addresses refer to the existing personal deployment. Use your own addresses and credentials when deploying another instance.

| Component | Address or file |
| --- | --- |
| Server management | [Railway: remarkabl](https://railway.com/project/045103b6-07cd-4c46-a902-22a087215bd0) |
| Diary API | https://diary-production-763d.up.railway.app |
| Personal cloud | https://cloud-production-b67a.up.railway.app |
| Browser simulator | https://diary-production-763d.up.railway.app/simulator |
| Tablet configuration | `/home/root/remarkabl/device.env` |
| Local credentials and backups | `.secrets/`, excluded from Git |

`OPENROUTER_API_KEY` and `TELEGRAM_BOT_TOKEN` belong in Railway Variables for `diary`. On the tablet, `REMARKABL_TOKEN` must match the server's `DEVICE_TOKEN`; `REMARKABL_URL` is the diary API URL. `REMARKABL_IDLE_MS` sets the handwriting pause before submission, defaulting to `5000`.

Cloning the repository does not restore credentials, notes, conversation history, or server volumes. Transfer their backups separately. `.env.example` contains empty credential fields; the configured Telegram user ID is an access-control identifier, not an authentication token.

## Clone and run the server locally

Requirements: Git, Python 3.12+, and `uv`. Building the tablet application also requires Rust/Cargo and the ARMv7 target.

```sh
git clone https://github.com/AlexWortega/remarkabl.git
cd remarkabl
uv sync --python 3.12 --group dev
cp .env.example .env
```

Open `.env` in an editor. To try the server without paid API calls, set:

```dotenv
DEVICE_TOKEN=replace-with-your-own-random-token
MOCK_LLM=true
EMBEDDING_MODE=lexical
```

Generate your own random token locally:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Start the server:

```sh
uv run remarkabl --port 8000
```

Open http://127.0.0.1:8000/simulator and enter your `DEVICE_TOKEN`. Replies are synthetic when `MOCK_LLM=true`. For real replies, configure `OPENROUTER_API_KEY` and set `MOCK_LLM=false`. For semantic retrieval, run `uv sync --extra semantic --group dev` and set `EMBEDDING_MODE=semantic`. The first indexing run downloads the model.

Other bot and cloud settings are listed in [.env.example](.env.example). Change `TELEGRAM_ALLOWED_USER_ID` to your own Telegram user ID for a separate deployment. Local scan processing requires Tesseract with `rus` and `eng` language data; these are included in the server Docker image.

## Update an already configured tablet

These commands assume XOVI/AppLoad and the personal cloud connection are already configured. The update script checks the model and firmware; it does not automatically support other versions.

1. Save a backup and close the diary.
2. Connect the tablet to your computer over USB and unlock it.
3. Update the source and build the application:

```sh
git pull --ff-only
cargo install cargo-zigbuild --locked
rustup target add armv7-unknown-linux-musleabihf
sh scripts/build-tablet.sh
```

The build is placed in `dist/remarkabl/`. When connecting from this computer for the first time, run:

```sh
sh scripts/pair-device.sh
```

The tablet's root password is shown under **Settings → Help → Copyrights and licenses**. Enter it locally when SSH asks. The script adds a dedicated SSH key stored in `.secrets/ssh/remarkabl`. If you restored that key from your private backup, pairing again is unnecessary.

Install the update:

```sh
sh scripts/update-device.sh
```

The script replaces the application, QML overlay, and service definitions. To load overlay changes, finish writing any notes and restart the stock interface:

```sh
ssh -i .secrets/ssh/remarkabl root@10.11.99.1 \
  'systemctl restart xochitl'
```

Unlock the tablet again after the restart. For a different USB/IP address, pass it as an argument to `update-device.sh`. Pairing uses the `REMARKABL_DEVICE_HOST` environment variable.

### A new tablet or computer

A fresh tablet requires separate installation of XOVI, AppLoad, and rmfakecloud-proxy, pairing with the personal cloud, and creation of `/home/root/remarkabl/device.env`. This repository does not yet provide a complete installer for a fresh tablet. Verified release versions and SHA256 checksums are in [deploy/upstream-manifest.json](deploy/upstream-manifest.json): XOVI `v19-23052026`, AppLoad `v0.3.1`, and proxy `v0.0.11`. Substituting a newer AppLoad build can break compatibility with the tablet's Qt version.

On a new computer, restore your private `.secrets/` directory separately or pair again. An existing `/home/root/remarkabl/device.env` stays on the tablet. If it is missing, create it there with permissions `600`, following this template:

```dotenv
REMARKABL_URL=https://your-diary-server.example
REMARKABL_TOKEN=the-same-value-as-server-DEVICE_TOKEN
REMARKABL_DATA_DIR=/home/root/remarkabl
REMARKABL_IDLE_MS=5000
```

## Update the Railway services

Requirements: Railway CLI, access to the existing project, and the configured Variables. From the repository root:

```sh
python3 scripts/deploy-server.py
railway link --project 045103b6-07cd-4c46-a902-22a087215bd0
railway up deploy/cloud --path-as-root --service cloud --detach
```

The first command deploys the diary; the last deploys the cloud. For another project, `deploy-server.py` accepts `--project`, `--service`, and `--environment`. Point `railway link` at that project as well.

The diary script creates a temporary directory containing only the required server files. Use it to upload server source without including your working directory's private files. These commands submit builds; wait for a successful deployment status in Railway.

Each service has its own volume mounted at `/data`. Diary settings are listed in `.env.example`. The cloud requires `CLOUD_USER`, `CLOUD_PASSWORD`, and `JWT_SECRET_KEY`; the diary's `RMFAKECLOUD_USER` and `RMFAKECLOUD_PASSWORD` must match the cloud owner account. Keep one continuously running replica for the bot and SQLite queue. Infrastructure is defined in [.railway/railway.ts](.railway/railway.ts); inspect its plan before applying it to a different project.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| SSH does not connect | Unlock the tablet, reconnect the USB cable, and check `10.11.99.1`. |
| Apps / AppLoad is missing | Firmware version, `remarkabl-xovi` status, and extension compatibility. |
| A question has no reply | Wi-Fi, `remarkabl-sync`, the URL/token in `device.env`, and authenticated `/v1/status`. |
| The bot rejects a document | Private chat, allowed Telegram user ID, file size, and URL accessibility. |
| A book has not appeared | `/status`, `/library`, publication state, and normal tablet sync. |
| PDF capture reports an error | Device/firmware compatibility and `/home/root/remarkabl/pdf-inbox/capture.log`. |

Inspect service state and recent sync messages:

```sh
ssh -i .secrets/ssh/remarkabl root@10.11.99.1 \
  'systemctl status remarkabl-sync remarkabl-xovi --no-pager; journalctl -u remarkabl-sync -n 40 --no-pager'
```

Test screen capture locally without submitting anything to an LLM:

```sh
ssh -i .secrets/ssh/remarkabl root@10.11.99.1 \
  '/home/root/xovi/exthome/appload/remarkabl/remarkabl-diary --snapshot /tmp/page.png'
```

### Restore the stock interface

```sh
sh scripts/restore-stock.sh
```

This disables automatic extension startup and restores the stock interface. Notes and the personal cloud connection are retained. Rebooting alone does not disable the configured XOVI startup service.

### Backups

Before the original installation, a local backup was saved as `.secrets/device-backup-3.20.0.92.tar.gz`, with its file list in `.secrets/backup-file-list.txt`. That is a pre-installation snapshot, not a current backup of later notes. Earlier system settings are also stored on the tablet in `/home/root/remarkabl/system-before.tar.gz`.

Before a firmware upgrade, back up the current `/home/root/.local/share/remarkable/` and `/home/root/remarkabl/` directories, your SSH key, and both Railway volumes (`diary` and `cloud`). Keep those backups outside Git. Check extension and cloud-proxy compatibility before changing firmware versions.

## Source checks

```sh
uv run pytest -q
uv run ruff check remarkabl tests
cargo test --manifest-path tablet/Cargo.toml --locked
cargo clippy --manifest-path tablet/Cargo.toml --locked -- -D warnings
```

Third-party sources and licenses are listed in [THIRD_PARTY.md](THIRD_PARTY.md).
