"""Validate at connection time, then connect to the validated IP (DNS rebinding safe)."""

import asyncio
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend

from .errors import PermanentError


class PublicNetwork(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.backend = AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if port not in (80, 443):
            raise PermanentError("Для ссылок разрешены только порты HTTP/HTTPS.")
        results = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(result[4][0] for result in results))
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise PermanentError("Ссылка ведёт на внутренний или служебный адрес.")
        return await self.backend.connect_tcp(
            addresses[0], port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(self, *args, **kwargs):
        raise PermanentError("Unix-сокеты недоступны для загрузки статей.")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


def validate_url(url):
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise PermanentError("Нужна обычная ссылка HTTP или HTTPS без логина и пароля.")
    try:
        if parts.port not in (None, 80, 443):
            raise PermanentError("Для ссылок разрешены только порты HTTP/HTTPS.")
    except ValueError as exc:
        raise PermanentError("Некорректный адрес ссылки.") from exc


async def download_public(url: str, path: Path, limit: int) -> tuple[str, str]:
    transport = httpx.AsyncHTTPTransport(retries=0)
    # httpcore has no public backend argument in HTTPX; keep this small adapter tested.
    transport._pool._network_backend = PublicNetwork()
    async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=60) as client:
        for _ in range(6):
            validate_url(url)
            async with client.stream(
                "GET", url, headers={"User-Agent": "remarkabl/0.1 (personal reading library)"}
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise PermanentError("Сервер вернул перенаправление без адреса.")
                    url = str(response.url.join(location))
                    continue
                if response.status_code in (401, 403, 404):
                    raise PermanentError("Материал по ссылке недоступен. Пришлите PDF или EPUB.")
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and int(length) > limit:
                    raise PermanentError("Размер файла превышает лимит.")
                total = 0
                with path.open("wb") as file:
                    async for block in response.aiter_bytes():
                        total += len(block)
                        if total > limit:
                            raise PermanentError("Размер файла превышает лимит.")
                        file.write(block)
                return response.headers.get("content-type", ""), url
        raise PermanentError("Слишком много перенаправлений.")
