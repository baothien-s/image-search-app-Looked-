import hashlib
import ipaddress
import socket
import time
import warnings
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import urllib3
import certifi
from PIL import Image, UnidentifiedImageError

from .errors import AppError
from .security import safe_url

UPLOAD_LIMIT = 500_000
URL_LIMIT = 10_000_000
Image.MAX_IMAGE_PIXELS = 25_000_000
MIMES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    mime: str
    digest: str
    width: int
    height: int


def validate_image(data, maximum=UPLOAD_LIMIT):
    if not data or len(data) > maximum:
        raise AppError("image_size", f"Choose an image no larger than {maximum:,} bytes.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                fmt = image.format
                width, height = image.size
                if fmt not in MIMES or width * height > Image.MAX_IMAGE_PIXELS or max(width, height) > 10_000:
                    raise AppError("image_format", "Use a JPEG, PNG, or WebP image with at most 25 megapixels and 10,000 pixels per side.")
                if getattr(image, "is_animated", False):
                    raise AppError("image_format", "Choose a still image; animated images are not supported.")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()  # reject truncated JPEGs, too
        return ValidatedImage(data, MIMES[fmt], hashlib.sha256(data).hexdigest(), width, height)
    except AppError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AppError("image_format", "The image is damaged or unsupported. Choose a valid JPEG, PNG, or WebP.") from None


def public_addresses(host, port):
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
            raise AppError("image_url", "Image URLs must resolve only to public internet addresses.")
        return addresses
    except (socket.gaierror, ValueError):
        raise AppError("image_url", "The image host could not be resolved.") from None


def download_image(url, maximum=URL_LIMIT):
    """Bounded, no-proxy HTTP with validated and pinned DNS on every redirect.

    The connection goes to a validated IP, while TLS checks the original host.
    This avoids a second DNS lookup and private-network DNS rebinding.
    """
    deadline = time.monotonic() + 30
    for _ in range(4):
        normalized = safe_url(url)
        if not normalized:
            raise AppError("image_url", "Enter a public HTTP or HTTPS image URL without credentials, using port 80 or 443.")
        parts = urlsplit(normalized)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = public_addresses(host, port)
        if time.monotonic() >= deadline:
            raise AppError("image_timeout", "Downloading the image timed out. Try a smaller image or a different host.", 504)
        timeout = urllib3.Timeout(connect=5, read=8, total=max(1, deadline-time.monotonic()))
        if parts.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(addresses[0], port, timeout=timeout,
                                              server_hostname=host, assert_hostname=host, cert_reqs="CERT_REQUIRED",
                                              ca_certs=certifi.where())
        else:
            pool = urllib3.HTTPConnectionPool(addresses[0], port, timeout=timeout)
        response = None
        try:
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
            response = pool.urlopen("GET", target, headers={"Host": parts.netloc, "Accept-Encoding": "identity",
                                    "User-Agent": "Looked-Local/1.0"}, retries=False, redirect=False, preload_content=False)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise AppError("image_url", "The image host returned an invalid redirect.")
                url = urljoin(normalized, location)
                continue
            if response.status != 200:
                raise AppError("image_download", "The image host did not return an accessible image. Try uploading the file instead.")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise AppError("image_download", "The image host returned an unsupported encoded response. Try uploading the file.")
            content_length = response.headers.get("Content-Length", "")
            if content_length.isdigit() and int(content_length) > maximum:
                raise AppError("image_size", f"The remote image exceeds {maximum:,} bytes.")
            chunks, size = [], 0
            while True:
                chunk = response.read(64 * 1024, decode_content=False)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise AppError("image_size", f"The remote image exceeds {maximum:,} bytes.")
                if time.monotonic() > deadline:
                    raise AppError("image_timeout", "Downloading the image timed out.", 504)
                chunks.append(chunk)
            return validate_image(b"".join(chunks), maximum)
        except AppError:
            raise
        except (urllib3.exceptions.TimeoutError, TimeoutError):
            raise AppError("image_timeout", "Downloading the image timed out. Try uploading the file.", 504) from None
        except (urllib3.exceptions.HTTPError, OSError, ValueError):
            raise AppError("image_download", "Could not securely download the image. Try uploading the file.", 502) from None
        finally:
            if response:
                response.close()
            pool.close()
    raise AppError("image_url", "The image URL redirected too many times.")
