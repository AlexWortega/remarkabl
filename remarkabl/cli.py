import argparse
import logging

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Personal reMarkable diary server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # httpx's INFO logger includes URLs containing Telegram's bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run("remarkabl.app:create_app", factory=True, host=args.host, port=args.port)
