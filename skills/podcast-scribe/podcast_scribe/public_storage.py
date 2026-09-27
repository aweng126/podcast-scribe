"""Deterministic, digest-checked storage for public records above Git blob limits."""
from __future__ import annotations

import hashlib
import json
import re

from .public_site import loads_record
from .share import MAX_SUBMISSION_BYTES

PART_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = MAX_SUBMISSION_BYTES + 4096
MAX_MANIFEST_BYTES = 128 * 1024


def record_bytes(record: dict) -> bytes:
    # Compact wrapping stays below the pretty-printed submission limit plus
    # provenance overhead; indenting the nested submission would grow it.
    return (json.dumps(record, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _readable_bytes(record: dict, limit: int) -> bytes | None:
    """Format small records without allocating a full indented copy of a large one."""
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    payload = bytearray()
    for chunk in encoder.iterencode(record):
        encoded = chunk.encode("utf-8")
        if len(payload) + len(encoded) + 1 > limit:
            return None
        payload.extend(encoded)
    payload.append(10)
    return bytes(payload)


def stored_files(record: dict, number: int):
    """Yield (relative path, bytes), with the manifest last, in one atomic tree."""
    root = f"issue-{number}.json"
    readable = _readable_bytes(record, min(PART_BYTES, MAX_RECORD_BYTES))
    if readable is not None:
        yield root, readable
        return
    # Keep large records compact: extra nesting indentation must not reduce the
    # submission size limit. Even a single part gets a readable manifest.
    payload = record_bytes(record)
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("Public record is too large.")
    parts = []
    for index, offset in enumerate(range(0, len(payload), PART_BYTES)):
        chunk = payload[offset:offset + PART_BYTES]
        name = f"issue-{number}/part-{index:05d}.bin"
        parts.append({"name": name, "size": len(chunk), "sha256": hashlib.sha256(chunk).hexdigest()})
        yield name, chunk
    manifest = {"schema_version": 2, "storage": "chunked-json", "byte_length": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(), "parts": parts}
    readable_manifest = _readable_bytes(manifest, MAX_MANIFEST_BYTES)
    if readable_manifest is None:
        raise ValueError("Public storage manifest is too large.")
    yield root, readable_manifest


def storage_manifest(payload: bytes, number: int) -> dict | None:
    """Validate all paths and bounds before any part is read from disk or GitHub."""
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("Public record is too large.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate storage manifest property.")
            result[key] = value
        return result
    data = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        return None
    if (len(payload) > MAX_MANIFEST_BYTES or set(data) != {"schema_version", "storage", "byte_length", "sha256", "parts"}
            or type(data["schema_version"]) is not int or data["storage"] != "chunked-json"
            or type(data["byte_length"]) is not int or not 1 <= data["byte_length"] <= MAX_RECORD_BYTES
            or not isinstance(data["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", data["sha256"])
            or not isinstance(data["parts"], list) or not 1 <= len(data["parts"]) <= 256):
        raise ValueError("Invalid public storage manifest.")
    total = 0
    for index, part in enumerate(data["parts"]):
        if (not isinstance(part, dict) or set(part) != {"name", "size", "sha256"}
                or part["name"] != f"issue-{number}/part-{index:05d}.bin"
                or type(part["size"]) is not int or not 1 <= part["size"] <= PART_BYTES
                or not isinstance(part["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", part["sha256"])):
            raise ValueError("Invalid public storage part.")
        total += part["size"]
    if total != data["byte_length"]:
        raise ValueError("Public storage part lengths do not match.")
    return data


def load_stored_record(payload: bytes, number: int, read_part) -> dict:
    manifest = storage_manifest(payload, number)
    if manifest is None:
        return loads_record(payload)
    assembled = bytearray()
    for part in manifest["parts"]:
        chunk = read_part(part["name"], part["size"])
        if len(chunk) != part["size"] or hashlib.sha256(chunk).hexdigest() != part["sha256"]:
            raise ValueError("Public storage part digest does not match.")
        assembled.extend(chunk)
    if hashlib.sha256(assembled).hexdigest() != manifest["sha256"]:
        raise ValueError("Public storage record digest does not match.")
    return loads_record(bytes(assembled))
