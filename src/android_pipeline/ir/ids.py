from __future__ import annotations

import hashlib
import struct
from typing import Any

DOMAIN = b"android-ir-id-v1\x00"


def utf8_text(value: Any) -> str:
    """Return deterministic valid Unicode, replacing unpaired UTF-16 surrogates."""
    return "".join("\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char for char in str(value))


def stable_id(kind: str, *parts: Any) -> str:
    h = hashlib.sha256(DOMAIN)
    fields = (kind, *parts)
    for value in fields:
        if value is None:
            payload = b"\x00"
        elif isinstance(value, bytes):
            payload = b"\x01" + value
        else:
            payload = b"\x02" + utf8_text(value).encode("utf-8")
        h.update(struct.pack(">Q", len(payload)))
        h.update(payload)
    return h.hexdigest()


def class_id(apk_sha: str, dex_id: str, descriptor: str) -> str:
    return stable_id("class", apk_sha.lower(), dex_id, descriptor)


def method_id(apk_sha: str, dex_id: str, owner: str, name: str, prototype: str) -> str:
    return stable_id("method", apk_sha.lower(), dex_id, owner, name, prototype)


def external_method_id(signature: str) -> str:
    return stable_id("external-ref", signature)


def instruction_id(method: str, offset_bytes: int) -> str:
    return stable_id("instruction", method, offset_bytes)


def call_id(instruction: str, invoke_kind: str) -> str:
    return stable_id("call", instruction, invoke_kind)
