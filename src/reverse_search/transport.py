"""Getting a LOCAL image to a reverse-image-search provider.

The problem
-----------
Google Lens (via SerpAPI) fetches the query image over HTTP; it cannot accept a
local file. The task forbids standing up a website. So we need the image to be
briefly reachable by URL without operating any hosting infrastructure.

The approach
------------
Three transports, in order of preference:

1. `direct`   - the provider accepts raw bytes (TinEye, Bing Visual Search).
                No upload of any kind is needed. Preferred whenever available.
2. `provided` - the operator already has a public URL for the same image
                (e.g. it is already online). We download it and verify byte or
                perceptual equality with the local file before trusting it.
3. `ephemeral`- upload to a public paste-style file bin with a short retention
                (0x0.st, tmpfiles.org, uguu.se). These are third-party, throwaway
                endpoints; we do not run them and nothing persists.

Honesty notes recorded in the evidence record:
  * The transport actually used is always recorded, so a reviewer knows whether
    the image left the machine and where it went.
  * Ephemeral upload is opt-in via `--upload` because it transmits the input
    photograph to a third party. It is never done silently.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

import requests

from src.core.errors import ImageTransportError
from src.core.hashing import sha256_file, sha256_hex
from src.core import perceptual

#: Anonymous, short-retention file bins. Each entry is (name, uploader).
#: They are tried in order; the first success wins.
_DEFAULT_TIMEOUT = 60


@dataclass(frozen=True)
class TransportResult:
    """Where the provider will fetch the query image from, and how it got there."""

    #: One of: direct | provided | ephemeral
    mode: str
    #: Public URL, or "" for direct-bytes transports.
    url: str
    #: Which service handled it ("0x0.st", "operator-supplied", "none").
    service: str
    #: Whether the bytes at `url` were confirmed identical to the local file.
    remote_bytes_verified: bool = False
    #: SHA-256 of what the remote URL actually served, when we checked.
    remote_sha256: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _guess_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


# --- Ephemeral uploaders -------------------------------------------------
# Each returns a public URL string or raises. Kept tiny and independent so a
# dead service simply falls through to the next one.


def _upload_0x0(path: Path, timeout: int) -> str:
    with open(path, "rb") as handle:
        response = requests.post(
            "https://0x0.st",
            files={"file": (path.name, handle, _guess_mime(path))},
            data={"expires": "1"},  # hours
            headers={"User-Agent": "FaceID-Blockchain-Verification/2.0"},
            timeout=timeout,
        )
    response.raise_for_status()
    url = response.text.strip()
    if not url.startswith("http"):
        raise ValueError(f"unexpected response: {url[:120]}")
    return url


def _upload_tmpfiles(path: Path, timeout: int) -> str:
    with open(path, "rb") as handle:
        response = requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (path.name, handle, _guess_mime(path))},
            timeout=timeout,
        )
    response.raise_for_status()
    url = response.json()["data"]["url"]
    # tmpfiles returns a viewer page; /dl/ serves the raw bytes.
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def _upload_uguu(path: Path, timeout: int) -> str:
    with open(path, "rb") as handle:
        response = requests.post(
            "https://uguu.se/upload",
            files={"files[]": (path.name, handle, _guess_mime(path))},
            timeout=timeout,
        )
    response.raise_for_status()
    return response.json()["files"][0]["url"]


EPHEMERAL_UPLOADERS: list[tuple[str, Callable[[Path, int], str]]] = [
    ("0x0.st", _upload_0x0),
    ("tmpfiles.org", _upload_tmpfiles),
    ("uguu.se", _upload_uguu),
]


def download(url: str, timeout: int = _DEFAULT_TIMEOUT, max_bytes: int = 25_000_000) -> bytes:
    """Fetch bytes from a URL with a hard size cap.

    Used both to verify an operator-supplied URL and to pull candidate
    thumbnails for local perceptual corroboration.
    """
    response = requests.get(
        url,
        timeout=timeout,
        stream=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; FaceID-Verification/2.0)"},
    )
    response.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise ImageTransportError(
                f"Remote image at {url} exceeds the {max_bytes:,} byte limit.",
                remedy="Use a smaller image.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def verify_public_url(image_path: str | Path, url: str, timeout: int = _DEFAULT_TIMEOUT) -> TransportResult:
    """Confirm an operator-supplied public URL really serves the local image.

    Byte-identical is the strong case. If the host re-encoded the file (very
    common on CDNs), we fall back to a perceptual comparison so an honest
    re-encode still works - and we record which of the two checks passed.
    """
    path = Path(image_path)
    if not url.lower().startswith(("http://", "https://")):
        raise ImageTransportError(
            f"--image-url must be an http(s) URL, got: {url}",
            remedy="Supply a direct link to the image file.",
        )

    try:
        remote = download(url, timeout=timeout)
    except ImageTransportError:
        raise
    except requests.RequestException as exc:
        raise ImageTransportError(
            f"Could not download the supplied image URL: {exc}",
            remedy="Confirm the URL is public and serves the raw image (not an HTML page).",
        ) from exc

    local_digest = sha256_file(path)
    remote_digest = sha256_hex(remote)

    if local_digest == remote_digest:
        return TransportResult(
            mode="provided",
            url=url,
            service="operator-supplied",
            remote_bytes_verified=True,
            remote_sha256=remote_digest,
            note="Remote URL served bytes identical to the local input image.",
        )

    # Not byte-identical: check whether it is at least the same picture.
    try:
        local_fp = perceptual.fingerprint(Path(path).read_bytes())
        remote_fp = perceptual.fingerprint(remote)
    except Exception as exc:
        raise ImageTransportError(
            f"The supplied URL did not serve a decodable image ({exc}).",
            remedy="Link directly to the image file, not to a web page containing it.",
        ) from exc

    report = perceptual.compare(local_fp, remote_fp)
    if not report.corroborated:
        raise ImageTransportError(
            "The supplied --image-url does not serve the same picture as --image "
            f"(dHash distance {report.dhash_distance}, pHash distance {report.phash_distance}).",
            remedy="Point --image-url at the exact same photograph you passed to --image.",
        )
    return TransportResult(
        mode="provided",
        url=url,
        service="operator-supplied",
        remote_bytes_verified=False,
        remote_sha256=remote_digest,
        note=(
            "Remote URL served a re-encoded copy of the local image "
            f"(dHash distance {report.dhash_distance}, pHash distance {report.phash_distance})."
        ),
    )


def upload_ephemeral(image_path: str | Path, timeout: int = _DEFAULT_TIMEOUT) -> TransportResult:
    """Upload to the first available anonymous, short-retention file bin.

    This is the only code path that transmits the input photograph anywhere,
    and the CLI requires an explicit --upload flag to reach it.
    """
    path = Path(image_path)
    if not path.is_file():
        raise ImageTransportError(
            f"Cannot upload, file not found: {path}",
            remedy="Check the --image path.",
        )

    failures: list[str] = []
    for service, uploader in EPHEMERAL_UPLOADERS:
        try:
            url = uploader(path, timeout)
        except Exception as exc:  # noqa: BLE001 - try the next service
            failures.append(f"{service}: {type(exc).__name__}: {exc}")
            continue
        return TransportResult(
            mode="ephemeral",
            url=url,
            service=service,
            remote_bytes_verified=False,
            note=f"Uploaded to the anonymous short-retention bin {service} for the search provider to fetch.",
        )

    raise ImageTransportError(
        "Every anonymous upload service failed:\n  - " + "\n  - ".join(failures),
        remedy=(
            "Check network access, or supply your own public link with "
            "--image-url, or use a direct-upload provider with --provider tineye."
        ),
    )
