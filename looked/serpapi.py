import json
import logging
import re
import time

import requests

from .errors import AppError


class SerpApiClient:
    def __init__(self, api_key, session=None):
        self._api_key = api_key
        self.session = session or requests.Session()
        self.session.trust_env = False
        # Request URLs contain the key: never allow HTTP debug logging.
        for name in ("urllib3", "requests"):
            logging.getLogger(name).disabled = True
            logging.getLogger(name).propagate = False
        logging.getLogger("urllib3.connectionpool").disabled = True

    def _request(self, method, path, before_send, **kwargs):
        before_send()  # durable HTTP-attempt counter, even on a timeout
        response = None
        try:
            response = self.session.request(method, "https://serpapi.com" + path,
                                            timeout=(7, 45), allow_redirects=False, stream=True, **kwargs)
            if response.status_code in (401, 403):
                raise AppError("invalid_key", "SerpApi rejected the API key. Check SERPAPI_API_KEY in your local .env file and restart.", 401)
            if response.status_code == 429:
                raise AppError("provider_quota", "SerpApi reports a quota or rate limit. Check your account dashboard and wait for its reset.", 429)
            if response.status_code >= 500:
                raise AppError("provider_unavailable", "SerpApi is temporarily unavailable. Try again later; retries count as new searches.", 502)
            if not 200 <= response.status_code < 300:
                raise AppError("provider_request", "SerpApi rejected the request. Check that the image is publicly accessible or upload a supported file.", 502)
            chunks, size, deadline = [], 0, time.monotonic() + 90
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > 10_000_000 or time.monotonic() > deadline:
                    raise AppError("malformed_response", "SerpApi returned an oversized or incomplete response.", 502)
                chunks.append(chunk)
            try:
                payload = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError, RecursionError):
                raise AppError("malformed_response", "SerpApi returned invalid JSON. No result was cached.", 502) from None
            if not isinstance(payload, dict):
                raise AppError("malformed_response", "SerpApi returned an unexpected response. No result was cached.", 502)
            if "error" in payload:
                # Classify only. Never repeat or log the remote error text.
                error = payload["error"] if isinstance(payload["error"], str) else ""
                lower = error.lower()
                if "api key" in lower or "api_key" in lower or "unauthorized" in lower:
                    raise AppError("invalid_key", "SerpApi rejected the API key. Check your local .env file and restart.", 401)
                if any(word in lower for word in ("quota", "run out", "limit", "credits")):
                    raise AppError("provider_quota", "SerpApi reports a quota or rate limit. Check your account dashboard.", 429)
                if any(phrase in lower for phrase in ("hasn't returned any results", "has not returned any results", "did not match any documents")):
                    return {"search_metadata": {"status": "Success"}, "visual_matches": [], "exact_matches": []}
                raise AppError("provider_error", "SerpApi could not complete this search. Check the image and try again later.", 502)
            return payload
        except AppError:
            raise
        except requests.Timeout:
            raise AppError("timeout", "SerpApi timed out. The attempt remains counted because it may have reached the provider. No automatic retry was sent.", 504) from None
        except requests.RequestException:
            raise AppError("connection", "Could not reach SerpApi. Check your connection. No automatic retry was sent.", 502) from None
        finally:
            if response is not None:
                response.close()

    def search(self, *, parameters, image=None, image_url=None, refresh=False, before_send=lambda: None):
        params = {**parameters, "api_key": self._api_key}
        if refresh:
            params["no_cache"] = "true"
        if image is not None:
            extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[image.mime]
            uploaded = self._request("POST", "/image", before_send,
                                     data={"api_key": self._api_key},
                                     files={"image": ("image." + extension, image.data, image.mime)})
            image_id = uploaded.get("image_id")
            if not isinstance(image_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", image_id):
                raise AppError("malformed_response", "SerpApi did not return a valid image upload ID. No search was sent.", 502)
            params["image_id"] = image_id
        else:
            params["url"] = image_url
        return self._request("GET", "/search.json", before_send, params=params)
