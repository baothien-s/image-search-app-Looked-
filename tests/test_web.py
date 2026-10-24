import json
import uuid
from io import BytesIO

import pytest

from looked.config import Settings
from looked.images import validate_image
from looked.web import create_app


@pytest.fixture
def web(setup_service, image_bytes):
    settings, _, _, provider, _ = setup_service
    app = create_app(settings, client=provider, downloader=lambda url: validate_image(image_bytes))
    return app, app.test_client(), provider, settings


def headers(app):
    return {"X-CSRF-Token": app.extensions["looked"]["csrf"], "X-Request-ID": str(uuid.uuid4())}


def submit(web, image_bytes):
    app, client, _, _ = web
    response = client.post("/api/search", data={"image": (BytesIO(image_bytes), "mock.png")}, headers=headers(app))
    assert response.status_code == 200, response.get_json()
    return response.get_json()["search"]


def test_home_status_security_headers_and_no_credentials(web):
    app, client, provider, settings = web
    page = client.get("/")
    assert page.status_code == 200
    assert settings.api_key not in page.text
    assert "Google Lens results through SerpApi" in page.text
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert client.get("/api/status").get_json()["history"] == []
    assert client.get("/.env").status_code == 404
    assert not provider.calls


def test_reopen_export_preview_and_clear_never_call_provider(web, image_bytes):
    app, client, provider, settings = web
    search = submit(web, image_bytes)
    before = client.get("/api/status").get_json()["quota"]
    for _ in range(3):
        saved = client.get("/api/history/" + search["id"]).get_json()["search"]
        assert saved["cached"] and "image_bytes" not in saved
    for extension in ("csv", "json"):
        exported = client.get(f'/api/export/{search["id"]}.{extension}')
        assert exported.status_code == 200
        assert "attachment" in exported.headers["Content-Disposition"]
        assert settings.api_key not in exported.text
    assert client.get("/api/preview/" + search["id"]).mimetype == "image/jpeg"
    cleared = client.delete("/api/history", headers=headers(app))
    assert cleared.status_code == 200 and cleared.get_json()["quota"] == before
    assert client.get("/api/history/" + search["id"]).status_code == 404
    assert len(provider.calls) == 1


def test_csrf_and_foreign_origin_and_host_block_mutations(web):
    app, client, provider, _ = web
    assert client.post("/api/search").status_code == 403
    assert client.delete("/api/history").status_code == 403
    assert client.post("/api/search", headers={**headers(app), "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
    assert not provider.calls


def test_key_never_reaches_frontend_exports_or_sqlite(web, image_bytes):
    app, client, provider, settings = web
    secret = settings.api_key
    provider.payload = {"visual_matches": [{"title": "Mock " + secret, "source": secret,
        "link": "https://example.com/?key=" + secret, "thumbnail": "https://example.com/?api_key=" + secret}]}
    search = submit(web, image_bytes)
    assert secret not in json.dumps(search)
    for path in ("/api/status", "/api/history/" + search["id"], "/api/export/" + search["id"] + ".json", "/api/export/" + search["id"] + ".csv"):
        assert secret not in client.get(path).text
    with app.extensions["looked"]["db"].connect() as conn:
        rows = conn.execute("SELECT source, result FROM searches").fetchall()
    assert secret not in str([tuple(row) for row in rows])


def test_unexpected_exception_containing_key_is_not_exposed_or_logged(web, image_bytes, caplog):
    app, client, provider, settings = web
    provider.error = RuntimeError("MOCK INTERNAL " + settings.api_key)
    response = client.post("/api/search", data={"image": (BytesIO(image_bytes), "mock.png")}, headers=headers(app))
    assert response.status_code == 500
    assert settings.api_key not in response.text + caplog.text
    assert client.get("/api/status").get_json()["quota"]["month"]["used"] == 1


def test_request_id_replay_after_refresh(web, image_bytes):
    app, client, provider, _ = web
    search = submit(web, image_bytes)
    request_headers = headers(app)
    for _ in range(2):
        response = client.post("/api/search", data={"refresh_id": search["id"]}, headers=request_headers)
        assert response.status_code == 200
    assert len(provider.calls) == 2


def test_env_key_read_only_from_explicit_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "MOCK_ENV_NOT_USED")
    (tmp_path / ".env").write_text("SERPAPI_API_KEY=MOCK_FILE_KEY\nMONTHLY_SEARCH_LIMIT=9\n", encoding="utf-8")
    settings = Settings.load(tmp_path)
    assert settings.api_key == "MOCK_FILE_KEY" and settings.monthly_limit == 9
    assert "MOCK_FILE_KEY" not in repr(settings)


def test_invalid_env_error_omits_setting_value(tmp_path):
    (tmp_path / ".env").write_text("MONTHLY_SEARCH_LIMIT=MOCK_SECRET\n", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        Settings.load(tmp_path)
    assert "MOCK_SECRET" not in str(error.value)
