import hmac
import json
import secrets
import sqlite3
import uuid
from io import BytesIO

from flask import Flask, Response, jsonify, render_template, request
from PIL import Image, ImageOps
from werkzeug.exceptions import HTTPException

from .config import Settings
from .database import Database
from .errors import AppError
from .images import download_image
from .quota import QuotaManager
from .results import csv_export, public_entry
from .security import scrub
from .serpapi import SerpApiClient
from .service import SearchService


def create_app(settings=None, *, client=None, downloader=None, now=None):
    from pathlib import Path
    settings = settings or Settings.load(Path(__file__).resolve().parent.parent)
    assets = Path(__file__).resolve().parent.parent
    app = Flask(__name__, template_folder=str(assets / "templates"), static_folder=str(assets / "static"))
    app.config.update(MAX_CONTENT_LENGTH=1_100_000, TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"],
                      DEBUG=False, PROPAGATE_EXCEPTIONS=False)
    csrf = secrets.token_urlsafe(32)
    db = Database(settings.db_path)
    quota = QuotaManager(db, settings, now=now)
    service = SearchService(db, quota, client or SerpApiClient(settings.api_key), settings,
                            downloader=downloader or download_image)
    app.extensions["looked"] = {"db": db, "quota": quota, "service": service, "csrf": csrf}

    def json_response(value, status=200):
        return jsonify(scrub(value, settings.api_key)), status

    def saved(search_id):
        entry = db.get(search_id)
        if entry is None:
            raise AppError("not_found", "This saved search no longer exists.", 404)
        return entry

    @app.before_request
    def protect_local_app():
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                raise AppError("origin", "Open the app directly at its local address before making changes.", 403)
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), csrf):
                raise AppError("csrf", "This page has expired. Reload the app and try again.", 403)

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.errorhandler(AppError)
    def app_error(error):
        return json_response({"error": error.as_dict(), "quota": quota.snapshot()}, error.status)

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = "The upload is too large. Local files must be at most 500,000 bytes." if error.code == 413 else "The request could not be processed. Reload the app and try again."
        return json_response({"error": {"code": "request", "message": message}}, error.code)

    @app.errorhandler(Exception)
    def unexpected_error(error):
        # No traceback or exception text: requests exceptions can carry a key.
        code, message = ("database", "The local database is busy or unavailable. Check disk space and try again.") if isinstance(error, sqlite3.Error) else ("internal", "An internal error occurred. Restart the local app and try again.")
        return json_response({"error": {"code": code, "message": message}}, 500)

    @app.get("/")
    def index():
        return render_template("index.html", csrf=csrf)

    @app.get("/api/status")
    def status():
        return json_response({"key_configured": settings.key_configured, "quota": quota.snapshot(), "history": db.history()})

    @app.post("/api/search")
    def search():
        try:
            operation_id = str(uuid.UUID(request.headers.get("X-Request-ID", "")))
        except ValueError:
            raise AppError("request_id", "Reload the app and try again with a new search.") from None
        refresh_id = request.form.get("refresh_id")
        if refresh_id:
            result = service.run(refresh_id=refresh_id, operation_id=operation_id)
        else:
            upload = request.files.get("image")
            result = service.run(values=request.form,
                                 file_bytes=upload.read(500_001) if upload else None,
                                 filename=upload.filename if upload else "image",
                                 image_url=request.form.get("url", "").strip() or None,
                                 operation_id=operation_id)
        return json_response({"search": result, "quota": quota.snapshot()})

    @app.get("/api/history/<search_id>")
    def reopen(search_id):
        # This path never constructs a provider request, even with no key/quota.
        return json_response({"search": public_entry(saved(search_id), True), "quota": quota.snapshot()})

    @app.delete("/api/history")
    def clear_history():
        db.clear_history()
        return json_response({"history": [], "quota": quota.snapshot()})

    @app.get("/api/preview/<search_id>")
    def preview(search_id):
        entry = saved(search_id)
        with Image.open(BytesIO(entry["image_bytes"])) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((600, 600))
            output = BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=85)
        return Response(output.getvalue(), mimetype="image/jpeg")

    @app.get("/api/thumbnail/<search_id>/<int:index>")
    def thumbnail(search_id, index):
        results = saved(search_id)["result"]["results"]
        if index < 0 or index >= len(results) or not results[index]["thumbnail"]:
            raise AppError("thumbnail", "No thumbnail is available.", 404)
        image = download_image(results[index]["thumbnail"], maximum=2_000_000)
        # Decode/re-encode so result hosts never supply executable content.
        with Image.open(BytesIO(image.data)) as picture:
            picture = ImageOps.exif_transpose(picture)
            picture.thumbnail((600, 600))
            output = BytesIO()
            picture.convert("RGB").save(output, "JPEG", quality=82)
        return Response(output.getvalue(), mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/export/<search_id>.<format_name>")
    def export(search_id, format_name):
        entry = scrub(public_entry(saved(search_id), True), settings.api_key)
        if format_name == "csv":
            content, mime = csv_export(entry), "text/csv; charset=utf-8"
        elif format_name == "json":
            content, mime = json.dumps(entry, ensure_ascii=False, indent=2), "application/json; charset=utf-8"
        else:
            raise AppError("export", "Choose CSV or JSON.", 404)
        return Response(content, content_type=mime, headers={
            "Content-Disposition": f'attachment; filename="looked-{entry["id"][:12]}.{format_name}"'})

    return app
