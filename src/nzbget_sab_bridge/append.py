"""Validation and mapping for the NZBGet 21.1 append parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from .config import Settings
from .nzb_validation import NzbValidationError, decode_base64_nzb, validate_nzb_xml
from .rpc import InvalidParams, RpcRequest
from .translate.status import priority_to_sab

_MAX_FILENAME_CHARS = 255
_MAX_URL_CHARS = 8192
_ARCHIVE_SUFFIXES = (
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tbz",
    ".tbz2",
    ".tgz",
    ".xz",
    ".zip",
)


@dataclass(frozen=True, slots=True)
class AppendSubmission:
    kind: Literal["file", "url"]
    filename: str
    category: str
    sab_priority: int
    add_paused: bool
    file_content: bytes | None = None
    url: str | None = None


def parse_append_request(
    request: RpcRequest,
    *,
    method: str,
    settings: Settings,
) -> AppendSubmission:
    params = request.params
    # NZBGet 21.1 treats Priority as optional and consumes trailing PP parameter
    # pairs until exhausted. The published signature lists ten fields, but the
    # source accepts the eight-field form (no Priority) and nine-field forms
    # (with Priority, without the trailing PP slot, and vice versa).
    if not 8 <= len(params) <= 10:
        raise InvalidParams("Invalid parameter count")

    filename, content, category = params[:3]
    index = 3
    priority = 0
    if isinstance(params[index], int) and not isinstance(params[index], bool):
        priority = params[index]
        index += 1
    if len(params) < index + 5:
        raise InvalidParams("Invalid parameter count")
    add_to_top = params[index]
    add_paused = params[index + 1]
    dupe_key = params[index + 2]
    dupe_score = params[index + 3]
    dupe_mode = params[index + 4]
    index += 5
    trailing = params[index:]
    if trailing and (len(trailing) != 1 or trailing[0] != []):
        raise InvalidParams("Post-processing parameters are not supported by the SABnzbd bridge")

    if not isinstance(filename, str):
        raise InvalidParams("Invalid parameter (NZBFilename)")
    if not isinstance(content, str):
        raise InvalidParams("Invalid parameter (NZBContent)")
    if not isinstance(category, str):
        raise InvalidParams("Invalid parameter (Category)")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise InvalidParams("Invalid parameter (Priority)")
    if not -2_147_483_648 <= priority <= 2_147_483_647:
        raise InvalidParams("Invalid parameter (Priority)")
    if not isinstance(add_to_top, bool):
        raise InvalidParams("Invalid parameter (AddToTop)")
    if not isinstance(add_paused, bool):
        raise InvalidParams("Invalid parameter (AddPaused)")
    if not isinstance(dupe_key, str):
        raise InvalidParams("Invalid parameter (DupeKey)")
    if isinstance(dupe_score, bool) or not isinstance(dupe_score, int):
        raise InvalidParams("Invalid parameter (DupeScore)")
    if not isinstance(dupe_mode, str):
        raise InvalidParams("Invalid parameter (DupeMode)")

    if add_to_top:
        raise InvalidParams("AddToTop is not supported by the SABnzbd bridge")
    if dupe_key:
        raise InvalidParams("Non-default DupeKey is not supported by the SABnzbd bridge")
    if dupe_score != 0:
        raise InvalidParams("Non-default DupeScore is not supported by the SABnzbd bridge")
    if dupe_mode.strip().casefold() != "score":
        raise InvalidParams("Only DupeMode SCORE is supported by the SABnzbd bridge")
    if add_paused and priority != 0:
        raise InvalidParams(
            "AddPaused with non-Normal priority is not supported; SABnzbd cannot "
            "represent both settings in one add request"
        )

    normalized_category = _validate_text(category, "Category", max_chars=255, allow_empty=True)
    if _is_url(content):
        _validate_http_url(content)
        normalized_filename = _validate_filename(filename) if filename else ""
        return AppendSubmission(
            kind="url",
            filename=normalized_filename,
            category=normalized_category,
            sab_priority=-2 if add_paused else priority_to_sab(priority),
            add_paused=add_paused,
            url=content,
        )

    if method.casefold() == "appendurl":
        raise InvalidParams("appendurl requires an HTTP or HTTPS URL")
    if "://" in content[:32]:
        raise InvalidParams("Only HTTP and HTTPS URLs are supported")
    normalized_filename = _validate_filename(filename)
    if normalized_filename.lower().endswith(_ARCHIVE_SUFFIXES):
        raise InvalidParams("Archive uploads are not supported; submit a plain .nzb file")
    if not normalized_filename.lower().endswith(".nzb"):
        raise InvalidParams("Only plain .nzb file uploads are supported")
    try:
        decoded = decode_base64_nzb(content, max_decoded_bytes=settings.nzb_effective_decoded_bytes)
        validate_nzb_xml(decoded)
    except NzbValidationError as exc:
        raise InvalidParams(str(exc)) from None
    return AppendSubmission(
        kind="file",
        filename=normalized_filename,
        category=normalized_category,
        sab_priority=-2 if add_paused else priority_to_sab(priority),
        add_paused=add_paused,
        file_content=decoded,
    )


def _is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _validate_text(
    value: str,
    parameter: str,
    *,
    max_chars: int,
    allow_empty: bool = False,
) -> str:
    if (not value and not allow_empty) or len(value) > max_chars:
        raise InvalidParams(f"Invalid parameter ({parameter})")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise InvalidParams(f"Invalid parameter ({parameter})")
    return value


def _validate_filename(value: str) -> str:
    normalized = _validate_text(value, "NZBFilename", max_chars=_MAX_FILENAME_CHARS)
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise InvalidParams("Invalid parameter (NZBFilename)")
    return normalized


def _validate_http_url(value: str) -> None:
    if not value or len(value) > _MAX_URL_CHARS:
        raise InvalidParams("Invalid parameter (NZBContent)")
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
        raise InvalidParams("Invalid parameter (NZBContent)")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise InvalidParams("Invalid parameter (NZBContent)") from None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise InvalidParams("Only HTTP and HTTPS URLs are supported")
