import asyncio
import json

import httpx
import pytest

from remarkabl.llm import Oracle, partial_answer


def test_partial_json_decodes_arbitrary_unicode_and_escape_boundaries():
    answer = 'Привет, "читатель"!\nПуть \\ книга 🙂'
    payload = json.dumps({"answer": answer, "sources": []}, ensure_ascii=True)
    for index in range(len(payload) + 1):
        assert answer.startswith(partial_answer(payload[:index]))
    assert partial_answer(payload) == answer


@pytest.mark.asyncio
async def test_oracle_exposes_deltas_before_complete_json(settings, monkeypatch):
    settings.openrouter_api_key = "test-key"
    messages = []
    parts = ['{"answer":"', 'Привет', ', читатель', '!","sources":[]}']

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for part in parts:
                data = {"choices": [{"delta": {"content": part}}]}
                yield ("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode()
                await asyncio.sleep(0)
            yield b"data: [DONE]\n\n"

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, stream=Stream())

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))

    async def progress(text):
        messages.append(text)

    result = await Oracle(settings).complete([], on_delta=progress)
    assert messages == ["Привет", "Привет, читатель", "Привет, читатель!"]
    assert result["answer"] == messages[-1]


@pytest.mark.asyncio
async def test_sse_delivers_processing_then_final_snapshot(app, client, turn, auth):
    client.post("/v1/turns", json=turn, headers=auth)
    route = next(r for r in app.routes if r.path.endswith("/events"))
    response = await route.endpoint(turn["id"])
    iterator = response.body_iterator
    assert '"status": "queued"' in await anext(iterator)
    with app.state.store.connect() as db:
        db.execute("UPDATE turns SET status='processing',answer='Первая часть' WHERE id=?", (turn["id"],))
    assert "Первая часть" in await anext(iterator)
    with app.state.store.connect() as db:
        db.execute("UPDATE turns SET status='complete',answer='Полный ответ' WHERE id=?", (turn["id"],))
    assert "Полный ответ" in await anext(iterator)
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


@pytest.mark.asyncio
async def test_pdf_capture_uses_visible_page_as_first_source(app, client, turn, auth):
    turn["page_capture"] = True
    assert client.post("/v1/turns", json=turn, headers=auth).status_code == 202
    await app.state.worker.once(("turn",))
    result = client.get("/v1/turns/" + turn["id"], headers=auth).json()
    assert result["status"] == "complete"
    assert result["citations"][0]["document_id"] == "screen"
    assert result["citations"][0]["title"] == "PDF на экране"
    assert "page_context" not in result
