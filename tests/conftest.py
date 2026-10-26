import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from remarkabl.app import create_app
from remarkabl.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        device_token="test-token",
        mock_llm=True,
        embedding_mode="lexical",
        run_workers=False,
        retry_base_seconds=0,
        _env_file=None,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def turn():
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).line([(50, 80), (150, 150), (400, 90)], fill="black", width=4)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return {
        "id": "request-1",
        "conversation_id": "dialogue-1",
        "image_base64": base64.b64encode(out.getvalue()).decode(),
        "strokes": [[[50, 80, 2], [150, 150, 2], [400, 90, 2]]],
    }
