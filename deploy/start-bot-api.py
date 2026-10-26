"""Credentials remain in environment variables, never argv or supervisor logs."""
import os
import signal
import time
from pathlib import Path

if not os.environ.get("TELEGRAM_API_ID") or not os.environ.get("TELEGRAM_API_HASH"):
    print("Local Telegram Bot API awaits API ID/hash; hosted Bot API remains available.", flush=True)
    while True:
        time.sleep(60)

root = Path(os.environ.get("TELEGRAM_LOCAL_DIR", "/data/telegram"))
root.mkdir(parents=True, exist_ok=True, mode=0o700)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
loader = "/opt/telegram/lib/ld-musl-x86_64.so.1"
os.execv(loader, [loader, "--library-path", "/opt/telegram/lib:/opt/telegram/usr/lib",
                            "/opt/telegram/telegram-bot-api", "--local", "--http-ip-address=127.0.0.1",
                            "--http-port=8081", f"--dir={root}", "--verbosity=1"])
