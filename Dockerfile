FROM aiogram/telegram-bot-api:10.3-linuxamd64@sha256:4b8c214e1892ce8b3d483b61c0f4197c3c8a6c67acd65ebd0e51d14d0cdaad40 AS telegram

FROM ghcr.io/astral-sh/uv:0.12.3 AS uv
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng fonts-dejavu-core libssl3 zlib1g supervisor && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
# Keep the Alpine executable and its musl libraries isolated from Debian's libc.
COPY --from=telegram /usr/local/bin/telegram-bot-api /opt/telegram/telegram-bot-api
COPY --from=telegram /lib /opt/telegram/lib
COPY --from=telegram /usr/lib /opt/telegram/usr/lib
RUN /opt/telegram/lib/ld-musl-x86_64.so.1 --library-path /opt/telegram/lib:/opt/telegram/usr/lib /opt/telegram/telegram-bot-api --version
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY remarkabl ./remarkabl
COPY assets ./assets
RUN uv sync --frozen --no-dev --extra semantic
COPY deploy/supervisord.conf /etc/supervisor/conf.d/remarkabl.conf
COPY deploy/start-bot-api.py /app/deploy/start-bot-api.py
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2
EXPOSE 8000
CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/remarkabl.conf"]
