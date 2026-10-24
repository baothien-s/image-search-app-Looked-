import ipaddress
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit


def scrub(value, secret=""):
    """Only retain selected fields elsewhere; additionally redact literal secrets."""
    if isinstance(value, str):
        if secret:
            value = value.replace(secret, "[redacted]")
            value = value.replace(quote(secret, safe=""), "[redacted]")
        return value
    if isinstance(value, list):
        return [scrub(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item, secret) for key, item in value.items()}
    return value


def safe_url(value, secret=""):
    if not isinstance(value, str) or not value or len(value) > 8192:
        return None
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        return None
    if secret and secret in unquote(value):
        return None
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        if parts.scheme.lower() not in ("http", "https") or not host or parts.username is not None or parts.password is not None:
            return None
        if parts.port not in (None, 80, 443):
            return None
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global or address.is_multicast:
                return None
        except ValueError:
            if "." not in host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in host):
                return None
        # Never propagate a provider credential-bearing link, even if the key
        # differs from the configured key. No metadata endpoints are followed.
        if any(name.lower() in ("api_key", "apikey", "serpapi_api_key") for name, _ in parse_qsl(parts.query)):
            return None
        netloc = f"[{host}]" if ":" in host else host
        if parts.port and not (parts.scheme.lower() == "http" and parts.port == 80 or parts.scheme.lower() == "https" and parts.port == 443):
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, parts.fragment))
    except (ValueError, UnicodeError):
        return None
