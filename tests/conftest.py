import json
import socket
from io import BytesIO
from pathlib import Path

import pytest
import requests
from PIL import Image

from looked.config import Settings
from looked.database import Database
from looked.quota import QuotaManager
from looked.service import SearchService


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail immediately if any test accidentally attempts network access."""
    def blocked(*args, **kwargs):
        raise AssertionError("NETWORK DISABLED: tests must use clearly labeled mocks")
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def image_bytes():
    stream = BytesIO()
    Image.new("RGB", (20, 16), "#285d50").save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def visual_payload():
    return json.loads((Path(__file__).parent / "fixtures" / "visual.json").read_text(encoding="utf-8"))


class FakeClient:
    """Synthetic provider; before_send mirrors real upload/search accounting."""
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.error = None
        self.entered = None
        self.release = None

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("image") is not None:
            kwargs["before_send"]()
        kwargs["before_send"]()
        if self.entered:
            self.entered.set()
            assert self.release.wait(5), "Test did not release the mock provider"
        if self.error:
            raise self.error
        return self.payload


@pytest.fixture
def setup_service(tmp_path, visual_payload):
    settings = Settings(tmp_path, api_key="MOCK_SECRET_KEY_NEVER_LIVE")
    db = Database(settings.db_path)
    quota = QuotaManager(db, settings)
    client = FakeClient(visual_payload)
    service = SearchService(db, quota, client, settings)
    return settings, db, quota, client, service
