import json
from io import BytesIO

import pytest
import requests

from looked.errors import AppError
from looked.images import download_image, public_addresses, validate_image
from looked.serpapi import SerpApiClient


class MockResponse:
    """Synthetic HTTP response, never connected to a provider."""
    def __init__(self, payload=None, status=200, raw=None):
        self.status_code = status
        self.raw_data = raw if raw is not None else json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, size):
        yield self.raw_data

    def close(self):
        self.closed = True


class MockSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize("response,code", [
    (MockResponse(status=401), "invalid_key"), (MockResponse(status=403), "invalid_key"),
    (MockResponse(status=429), "provider_quota"), (MockResponse(status=503), "provider_unavailable"),
    (MockResponse(status=400), "provider_request"), (MockResponse(raw=b"<html>MOCK_SECRET</html>"), "malformed_response"),
    (MockResponse(payload=[]), "malformed_response"),
    (MockResponse(payload={"error": "Invalid API key: MOCK_SECRET"}), "invalid_key"),
    (MockResponse(payload={"error": "You have run out of searches: MOCK_SECRET"}), "provider_quota"),
    (MockResponse(payload={"error": "Unexpected mock failure MOCK_SECRET"}), "provider_error"),
    (requests.Timeout("MOCK_SECRET"), "timeout"), (requests.ConnectionError("MOCK_SECRET"), "connection")])
def test_api_errors_are_safe_no_retry(response, code):
    session = MockSession([response])
    client = SerpApiClient("MOCK_SECRET", session=session)
    sends = []
    with pytest.raises(AppError) as error:
        client.search(parameters={"engine": "google_lens", "type": "visual_matches"},
                      image_url="https://example.com/mock.png", before_send=lambda: sends.append(1))
    assert error.value.code == code and "MOCK_SECRET" not in str(error.value)
    assert len(session.calls) == len(sends) == 1


def test_upload_then_search_refresh_request_shape(image_bytes):
    session = MockSession([MockResponse({"image_id": "MOCK_upload_ID"}), MockResponse({"visual_matches": []})])
    client = SerpApiClient("MOCK_SECRET", session=session)
    calls = []
    client.search(parameters={"engine": "google_lens", "type": "visual_matches"}, image=validate_image(image_bytes),
                  refresh=True, before_send=lambda: calls.append(1))
    upload, search = session.calls
    assert upload[0] == "POST" and upload[1] == "https://serpapi.com/image"
    assert upload[2]["files"]["image"][1] == image_bytes
    assert search[2]["params"]["image_id"] == "MOCK_upload_ID"
    assert search[2]["params"]["no_cache"] == "true"
    assert "url" not in search[2]["params"]
    assert len(calls) == 2 and not session.trust_env
    assert not search[2]["allow_redirects"] and search[2]["timeout"] == (7, 45)


@pytest.mark.parametrize("payload", [{}, {"image_id": []}, {"image_id": "invalid/id"}])
def test_malformed_upload_never_starts_search(payload, image_bytes):
    session = MockSession([MockResponse(payload)])
    with pytest.raises(AppError):
        SerpApiClient("MOCK_SECRET", session).search(parameters={}, image=validate_image(image_bytes))
    assert len(session.calls) == 1


def test_known_no_results_error_becomes_empty_results():
    session = MockSession([MockResponse({"error": "Google Lens hasn't returned any results for this image."})])
    payload = SerpApiClient("MOCK_SECRET", session).search(parameters={}, image_url="https://example.com/mock.png")
    assert payload["visual_matches"] == []


def test_image_validation_and_content_hash(image_bytes):
    image = validate_image(image_bytes)
    assert image.mime == "image/png" and image.width == 20 and len(image.digest) == 64
    assert image.digest == validate_image(image_bytes).digest


@pytest.mark.parametrize("raw", [b"", b"not an image", b"<svg>mock</svg>", b"x" * 500001],
                         ids=["empty", "invalid-bytes", "svg", "oversized"])
def test_bad_images_rejected(raw):
    with pytest.raises(AppError):
        validate_image(raw)


def test_private_dns_answer_blocks_public_looking_hostname(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(AppError, match="public"):
        public_addresses("example.com", 443)


def test_download_pins_dns_and_preserves_tls_hostname(monkeypatch, image_bytes):
    calls = []
    class Pool:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))
        def urlopen(self, method, target, **kwargs):
            self.status, self.headers, self.buffer = 200, {}, BytesIO(image_bytes)
            assert kwargs["headers"]["Host"] == "example.com"
            assert kwargs["retries"] is False and kwargs["redirect"] is False
            return self
        def read(self, size, **kwargs):
            return self.buffer.read(size)
        def close(self):
            pass
    monkeypatch.setattr("looked.images.public_addresses", lambda *args: ["93.184.216.34"])
    monkeypatch.setattr("looked.images.urllib3.HTTPSConnectionPool", Pool)
    assert download_image("https://example.com/mock.png").data == image_bytes
    assert calls[0][0][0] == "93.184.216.34"
    assert calls[0][1]["server_hostname"] == calls[0][1]["assert_hostname"] == "example.com"


def test_redirect_to_private_address_is_rejected(monkeypatch):
    class Pool:
        status = 302
        headers = {"Location": "http://127.0.0.1/private"}
        def __init__(self, *args, **kwargs):
            pass
        def urlopen(self, *args, **kwargs):
            return self
        def close(self):
            pass
    monkeypatch.setattr("looked.images.public_addresses", lambda *args: ["93.184.216.34"])
    monkeypatch.setattr("looked.images.urllib3.HTTPSConnectionPool", Pool)
    with pytest.raises(AppError):
        download_image("https://example.com/redirect")
