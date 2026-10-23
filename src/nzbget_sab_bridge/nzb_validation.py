"""Strict validation for plain NZB files submitted through append.

The parser deliberately rejects DTDs, entity declarations, and external
entities. It never resolves external resources and never logs payload bytes.
"""

from __future__ import annotations

import base64
import binascii
from xml.parsers import expat


class NzbValidationError(ValueError):
    """A safe, payload-free NZB validation failure."""


def decode_base64_nzb(encoded: str, *, max_decoded_bytes: int) -> bytes:
    if not encoded:
        raise NzbValidationError("NZB content is empty")
    max_encoded = 4 * ((max_decoded_bytes + 2) // 3)
    if len(encoded) > max_encoded:
        raise NzbValidationError("NZB content exceeds the decoded size limit")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise NzbValidationError("NZB content is not valid base64") from None
    if not decoded:
        raise NzbValidationError("NZB content is empty")
    if len(decoded) > max_decoded_bytes:
        raise NzbValidationError("NZB content exceeds the decoded size limit")
    return decoded


def validate_nzb_xml(content: bytes) -> None:
    if not content:
        raise NzbValidationError("NZB content is empty")
    if content[:2] == b"\x1f\x8b" or content[:2] == b"BZ":
        raise NzbValidationError("Compressed NZB payloads are not supported")

    root: str | None = None
    file_count = 0
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def reject_doctype(*_args: object) -> None:
        raise NzbValidationError("DTD and entity declarations are not supported")

    def reject_entity(*_args: object) -> None:
        raise NzbValidationError("DTD and entity declarations are not supported")

    def reject_external(*_args: object) -> int:
        return 0

    def start_element(name: str, _attrs: dict[str, str]) -> None:
        nonlocal root, file_count
        local_name = name.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()
        if root is None:
            root = local_name
        if local_name == "file":
            file_count += 1

    parser.StartDoctypeDeclHandler = reject_doctype
    parser.EntityDeclHandler = reject_entity
    parser.ExternalEntityRefHandler = reject_external
    parser.StartElementHandler = start_element

    try:
        parser.Parse(content, True)
    except NzbValidationError:
        raise
    except expat.ExpatError:
        raise NzbValidationError("NZB content is not well-formed XML") from None

    if root != "nzb":
        raise NzbValidationError("NZB root element must be <nzb>")
    if file_count == 0:
        raise NzbValidationError("NZB contains no <file> entries")
