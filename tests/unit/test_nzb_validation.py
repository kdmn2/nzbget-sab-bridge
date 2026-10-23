from __future__ import annotations

import base64

import pytest

from nzbget_sab_bridge.nzb_validation import (
    NzbValidationError,
    decode_base64_nzb,
    validate_nzb_xml,
)

_VALID_NZB = (
    b'<?xml version="1.0"?><nzb><file subject="synthetic">'
    b"<groups><group>alt.test</group></groups>"
    b'<segments><segment bytes="1" number="1">a@b</segment></segments>'
    b"</file></nzb>"
)


def test_valid_nzb_xml() -> None:
    validate_nzb_xml(_VALID_NZB)


@pytest.mark.parametrize(
    "content",
    [
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
        b"<file><groups><group>alt.test</group></groups></file></nzb>",
        b'<?xml version="1.0"?><nzb:nzb xmlns:nzb="synthetic"><nzb:file>'
        b"<nzb:groups/><nzb:segments/></nzb:file></nzb:nzb>",
        b"\xef\xbb\xbf<?xml version='1.0'?><nzb><file/></nzb>",
        b"<?xml version='1.0'?><!-- comment --><nzb><file/></nzb>",
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
        b"  <file><groups/><segments/></file>\n</nzb>\n",
    ],
)
def test_valid_namespaces_declarations_and_whitespace_are_accepted(content: bytes) -> None:
    validate_nzb_xml(content)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"<nzb>",
        b"<notnzb><file/></notnzb>",
        b"<nzb></nzb>",
        b"<nzb><notfile/></nzb>",
    ],
)
def test_invalid_nzb_xml_is_rejected(content: bytes) -> None:
    with pytest.raises(NzbValidationError):
        validate_nzb_xml(content)


@pytest.mark.parametrize(
    "content",
    [
        b'<?xml version="1.0"?><!DOCTYPE nzb><nzb><file/></nzb>',
        b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY x "boom">]><nzb><file>&x;</file></nzb>',
        b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY xxe SYSTEM '
        b'"file:///etc/passwd">]><nzb><file>&xxe;</file></nzb>',
    ],
)
def test_dtd_and_entities_are_rejected(content: bytes) -> None:
    with pytest.raises(NzbValidationError):
        validate_nzb_xml(content)


@pytest.mark.parametrize("content", [b"\x1f\x8bnot-an-nzb", b"BZnot-an-nzb"])
def test_compressed_payloads_are_rejected(content: bytes) -> None:
    with pytest.raises(NzbValidationError, match="Compressed"):
        validate_nzb_xml(content)


def test_base64_validation_and_decoded_limit() -> None:
    content = base64.b64encode(_VALID_NZB).decode()
    assert decode_base64_nzb(content, max_decoded_bytes=len(_VALID_NZB)) == _VALID_NZB
    with pytest.raises(NzbValidationError, match="base64"):
        decode_base64_nzb("not-base64!", max_decoded_bytes=1024)
    with pytest.raises(NzbValidationError, match="size limit"):
        decode_base64_nzb(content, max_decoded_bytes=10)
    with pytest.raises(NzbValidationError, match="empty"):
        decode_base64_nzb("", max_decoded_bytes=1024)
