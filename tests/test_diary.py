import copy
import time

import pytest

from remarkabl.app import create_app
from remarkabl.errors import NotConfigured, RetryableError


def test_private_routes_require_token(client, turn):
    assert client.get("/health").status_code == 200
    for path in ("/v1/library", "/v1/status", "/v1/turns/request-1"):
        assert client.get(path).status_code == 401
    assert client.post("/v1/turns", json=turn).status_code == 401


@pytest.mark.asyncio
async def test_same_question_survives_restart_and_executes_once(client, app, settings, turn, auth):
    first = client.post("/v1/turns", json=turn, headers=auth)
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert client.post("/v1/turns", json=turn, headers=auth).status_code == 202
    restarted = create_app(settings)
    assert restarted.state.store.turn(turn["id"])["status"] == "queued"
    assert await restarted.state.worker.once(("turn",))
    assert not await restarted.state.worker.once(("turn",))
    result = client.get("/v1/turns/request-1", headers=auth).json()
    assert result["status"] == "complete"
    assert result["transcription"]
    assert result["answer"]
    assert "image_path" not in result and "input_hash" not in result
    assert client.post("/v1/turns", json=turn, headers=auth).json()["answer"] == result["answer"]


def test_id_reuse_with_different_question_is_rejected(client, auth, turn):
    client.post("/v1/turns", json=turn, headers=auth)
    other = copy.deepcopy(turn)
    other["conversation_id"] = "different-dialogue"
    assert client.post("/v1/turns", json=other, headers=auth).status_code == 409
    other = copy.deepcopy(turn)
    other["strokes"] = []
    assert client.post("/v1/turns", json=other, headers=auth).status_code == 409


def test_invalid_image_and_path_ids_rejected(client, auth, turn):
    turn["image_base64"] = "not-a-png"
    assert client.post("/v1/turns", json=turn, headers=auth).status_code == 422
    turn["id"] = "../../escape"
    assert client.post("/v1/turns", json=turn, headers=auth).status_code == 422


@pytest.mark.asyncio
async def test_transient_failure_preserves_question_and_ocr(client, app, auth, turn, monkeypatch):
    client.post("/v1/turns", json=turn, headers=auth)
    oracle = app.state.worker.oracle
    original = oracle.answer

    async def fail(*args):
        raise RetryableError("rate limited")

    monkeypatch.setattr(oracle, "answer", fail)
    await app.state.worker.once(("turn",))
    assert app.state.store.turn(turn["id"])["status"] == "queued"
    assert app.state.store.turn(turn["id"])["transcription"]

    async def do_not_repeat_ocr(*args):
        raise AssertionError("OCR must not run twice")

    monkeypatch.setattr(oracle, "transcribe", do_not_repeat_ocr)
    monkeypatch.setattr(oracle, "answer", original)
    await app.state.worker.once(("turn",))
    assert app.state.store.turn(turn["id"])["status"] == "complete"


@pytest.mark.asyncio
async def test_missing_credentials_do_not_exhaust_retries(client, app, turn, auth, monkeypatch):
    client.post("/v1/turns", json=turn, headers=auth)

    async def missing(*args):
        raise NotConfigured("not configured")

    monkeypatch.setattr(app.state.worker.oracle, "transcribe", missing)
    for _ in range(10):
        await app.state.worker.once(("turn",))
    assert app.state.store.turn(turn["id"])["status"] == "queued"
    with app.state.store.connect() as db:
        assert (
            db.execute("SELECT attempts FROM jobs WHERE id=?", ("turn:" + turn["id"],)).fetchone()[
                0
            ]
            == 0
        )


def test_expired_job_lease_is_recovered(app):
    store = app.state.store
    store.enqueue("turn:a", "turn", "a")
    first = store.claim(("turn",))
    assert first["attempts"] == 1
    assert store.claim(("turn",)) is None
    with store.connect() as db:
        db.execute("UPDATE jobs SET lease=?", (time.time() - 1,))
    recovered = store.claim(("turn",))
    assert recovered["id"] == first["id"]
    assert recovered["attempts"] == 2


def test_body_limit_applies_before_parsing(app, auth):
    from fastapi.testclient import TestClient

    app.state.settings.max_body_bytes = 20
    # The middleware was configured with the original limit, so test the actual deployed bound.
    with TestClient(app) as client:
        response = client.post(
            "/v1/turns", headers={**auth, "Content-Length": str(20 * 1024 * 1024)}, content=b"{}"
        )
    assert response.status_code == 413
