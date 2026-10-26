from pathlib import Path

import httpx

from .config import Settings
from .errors import NotConfigured, PermanentError, RetryableError


class Cloud:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def rename(self, document):
        s = self.settings
        async with httpx.AsyncClient(base_url=s.rmfakecloud_url.rstrip("/"), timeout=60) as client:
            login = await client.post(
                "/ui/api/login",
                json={"email": s.rmfakecloud_user, "password": s.rmfakecloud_password},
            )
            if login.status_code in (401, 403):
                raise NotConfigured("Не удалось войти в личное облако.")
            login.raise_for_status()
            client.headers["Authorization"] = "Bearer " + login.text.strip()
            response = await client.put(
                "/ui/api/documents",
                json={
                    "documentId": document["cloud_id"],
                    "name": document["title"],
                    "parentId": "",
                },
            )
            response.raise_for_status()
            response = await client.get("/ui/api/sync")
            response.raise_for_status()

    async def publish(self, document):
        s = self.settings
        if not s.rmfakecloud_url or not s.rmfakecloud_user or not s.rmfakecloud_password:
            raise NotConfigured("Личное облако ещё не настроено. Документ сохранён на сервере.")
        async with httpx.AsyncClient(base_url=s.rmfakecloud_url.rstrip("/"), timeout=180) as client:
            login = await client.post(
                "/ui/api/login",
                json={"email": s.rmfakecloud_user, "password": s.rmfakecloud_password},
            )
            if login.status_code in (401, 403):
                raise NotConfigured("Не удалось войти в личное облако.")
            login.raise_for_status()
            # Use bearer authentication as well as cookies: private HTTP must work with
            # Secure cookies issued by the public HTTPS rmfakecloud deployment.
            client.headers["Authorization"] = "Bearer " + login.text.strip()
            path = Path(document["reading_path"])
            # Stable name lets rmfakecloud return docId on conflict after a crash/retry.
            name = f"{document['title']} [{document['id'][:12]}]{path.suffix}"
            with path.open("rb") as file:
                response = await client.post(
                    "/ui/api/documents/upload",
                    files={
                        "file": (
                            name,
                            file,
                            "application/pdf" if path.suffix == ".pdf" else "application/epub+zip",
                        )
                    },
                    data={"parent": "root"},
                )
            if response.status_code == 409 and response.json().get("docId"):
                return response.json()["docId"]
            if response.status_code >= 500:
                raise RetryableError("Личное облако временно недоступно.")
            if response.status_code >= 400:
                raise PermanentError("Личное облако отклонило документ.")
            docs = response.json()
            if not docs:
                raise RetryableError("Облако не подтвердило загрузку.")
            return docs[0].get("ID") or docs[0].get("id")
