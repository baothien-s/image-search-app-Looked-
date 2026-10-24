import csv
import hashlib
import io
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import AppError
from .security import safe_url, scrub

SOCIAL_DOMAINS = ("facebook.com", "instagram.com", "threads.net", "threads.com", "x.com", "twitter.com",
                  "tiktok.com", "pinterest.com", "reddit.com", "linkedin.com", "youtube.com", "youtu.be",
                  "tumblr.com", "bsky.app", "vk.com", "weibo.com", "snapchat.com")


def parameters_from(values):
    search_type = values.get("type", "visual_matches")
    language = values.get("hl", "en")
    country = values.get("country", "")
    if search_type not in ("visual_matches", "exact_matches"):
        raise AppError("parameters", "Choose visual matches or the provider's exact matches tab.")
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2}|-[0-9]{3})?", language):
        raise AppError("parameters", "Enter a language code such as en or vi.")
    if not isinstance(country, str) or country and not re.fullmatch(r"[a-z]{2}", country):
        raise AppError("parameters", "Enter a two-letter country code or leave it blank.")
    params = {"engine": "google_lens", "type": search_type, "hl": language, "safe": "active"}
    if country:
        params["country"] = country
    return params


def cache_key(image_hash, parameters):
    # Versioned so a future normalization change cannot reuse incompatible data.
    text = json.dumps({"schema": 1, "sha256": image_hash, "parameters": parameters}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def page_identity(link):
    parts = urlsplit(link)
    # Do not conflate arbitrary query strings, paths or HTTP with HTTPS.
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid", "dclid")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def parse_results(payload, search_type, secret=""):
    if not isinstance(payload, dict) or "error" in payload:
        raise AppError("malformed_response", "SerpApi returned an unexpected response. No result was cached.", 502)
    metadata = payload.get("search_metadata", {})
    if not isinstance(metadata, dict):
        raise AppError("malformed_response", "SerpApi returned invalid search metadata.", 502)
    status = metadata.get("status")
    if status is not None and status != "Success":
        raise AppError("provider_incomplete", "SerpApi did not return a completed search. No result was cached.", 502)
    if search_type not in payload and status != "Success":
        raise AppError("malformed_response", "SerpApi returned no recognizable result section.", 502)
    entries = payload.get(search_type, [])
    if not isinstance(entries, list):
        raise AppError("malformed_response", "SerpApi returned an invalid result list. No result was cached.", 502)
    results, seen, duplicates, skipped = [], set(), 0, 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            skipped += 1
            continue
        link = safe_url(entry.get("link"), secret)
        if link:
            identity = page_identity(link)
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
        title = entry.get("title")
        source = entry.get("source")
        position = entry.get("position")
        results.append({
            "provider_order": index + 1,
            "position": position if type(position) is int and position > 0 else None,
            "title": scrub(title[:2000], secret) if isinstance(title, str) and title.strip() else "Untitled result",
            "source": scrub(source[:500], secret) if isinstance(source, str) else None,
            "domain": urlsplit(link).hostname if link else None,
            "link": link,
            "thumbnail": safe_url(entry.get("thumbnail"), secret),
            # visual_matches.exact_matches is a navigation flag, not proof that
            # the visual result itself is exact. Only the exact tab supports it.
            "match_label": "Provider exact-match result" if search_type == "exact_matches" else "Visual result",
        })
    warnings = []
    if skipped:
        warnings.append(f"Skipped {skipped} malformed result entries.")
    if search_type not in payload:
        warnings.append("The completed response did not include the requested result section.")
    if entries and not results and skipped:
        raise AppError("malformed_response", "SerpApi returned only malformed result entries. No result was cached.", 502)
    return {"results": results, "duplicates_removed": duplicates, "warnings": warnings,
            "social_domains": [domain for domain in dict.fromkeys(r["domain"] for r in results if r["domain"])
                               if any(domain == social or domain.endswith("." + social) for social in SOCIAL_DOMAINS)]}


def csv_export(entry):
    stream = io.StringIO(newline="")
    columns = ["provider_order", "position", "title", "source", "domain", "link", "thumbnail", "match_label"]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in entry["result"]["results"]:
        safe = {}
        for name in columns:
            value = row.get(name)
            # CSV quoting alone does not stop spreadsheet formula execution.
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe[name] = value
        writer.writerow(safe)
    return "\ufeff" + stream.getvalue()


def public_entry(entry, cached):
    return {key: value for key, value in {**entry, "cached": cached}.items() if key not in ("image_bytes", "mime")}
