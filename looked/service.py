import json
import re
import uuid
from datetime import datetime, timezone

from .errors import AppError
from .images import download_image, validate_image
from .results import cache_key, parameters_from, parse_results, public_entry
from .security import safe_url, scrub


class SearchService:
    def __init__(self, database, quota, client, settings, downloader=download_image):
        self.db, self.quota, self.client, self.settings = database, quota, client, settings
        self.downloader = downloader

    def _replay(self, operation_id):
        operation = self.db.operation(operation_id)
        if operation:
            if operation["error"]:
                error = json.loads(operation["error"])
                raise AppError(error["code"], error["message"], operation["status"])
            cached = self.db.get(operation["search_id"])
            if cached:
                return public_entry(cached, True)
        return None

    def run(self, *, values=None, file_bytes=None, filename="image", image_url=None,
            refresh_id=None, operation_id=None):
        operation_id = operation_id or str(uuid.uuid4())
        replay = self._replay(operation_id)
        if replay:
            return replay
        with self.db.search_lock():
            replay = self._replay(operation_id)
            if replay:
                return replay
            attempt = None
            try:
                refresh = refresh_id is not None
                if refresh:
                    old = self.db.get(refresh_id)
                    if old is None:
                        raise AppError("not_found", "This saved search no longer exists.", 404)
                    parameters = old["parameters"]
                    source = old["source"]
                    image_url = source.get("url")
                    image = (self.downloader(image_url) if source["kind"] == "url"
                             else validate_image(old["image_bytes"]))
                else:
                    parameters = parameters_from(values or {})
                    if bool(file_bytes) == bool(image_url):
                        raise AppError("image_required", "Choose one image file or one public image URL.")
                    if image_url:
                        image_url = safe_url(image_url, self.settings.api_key)
                        if not image_url:
                            raise AppError("image_url", "Enter a public HTTP or HTTPS image URL without API credentials.")
                        source = {"kind": "url", "url": image_url, "label": image_url}
                        remembered_hash = self.db.url_hash(image_url)
                        if remembered_hash:
                            cached = self.db.get(cache_key(remembered_hash, parameters))
                            if cached:
                                self.db.update_source(cached["id"], source, alias=image_url)
                                cached["source"] = source
                                self.db.finish_operation(operation_id, cached["id"])
                                return public_entry(cached, True)
                        image = self.downloader(image_url)
                    else:
                        image = validate_image(file_bytes)
                        label = re.split(r"[/\\]", filename or "image")[-1][:160]
                        label = "".join(c for c in label if ord(c) >= 32)
                        source = {"kind": "file", "label": scrub(label or "image", self.settings.api_key)}
                key = cache_key(image.digest, parameters)
                if not refresh:
                    cached = self.db.get(key)
                    if cached:
                        self.db.update_source(key, source, alias=image_url)
                        cached["source"] = source
                        self.db.finish_operation(operation_id, key)
                        return public_entry(cached, True)
                if not self.settings.key_configured:
                    raise AppError("missing_key", "Add your SerpApi key to the local .env file and restart. Saved searches still work.", 400)
                attempt = self.quota.reserve()
                payload = self.client.search(parameters=parameters,
                    image=image if source["kind"] == "file" else None,
                    image_url=image_url, refresh=refresh, before_send=self.quota.record_http_request)
                result = parse_results(payload, parameters["type"], self.settings.api_key)
                entry = {"id": key, "image_hash": image.digest, "parameters": parameters,
                         "source": source, "result": result,
                         "created_at": datetime.now(timezone.utc).isoformat()}
                self.db.put(entry, image.data, image.mime, alias=image_url)
                self.quota.finish(attempt, "success")
                self.db.finish_operation(operation_id, key)
                return public_entry(entry, False)
            except AppError as error:
                if attempt:
                    self.quota.finish(attempt, error.code)
                self.db.finish_operation(operation_id, error=error)
                raise
            except Exception:
                error = AppError("internal", "An internal error occurred. Restart the local app and check history before trying again.", 500)
                if attempt:
                    self.quota.finish(attempt, "internal")
                self.db.finish_operation(operation_id, error=error)
                raise error from None
