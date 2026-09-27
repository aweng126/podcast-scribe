"""Readable public records remain digest-bound through chunked Git storage."""
from copy import deepcopy
import difflib
import hashlib
import json
import pytest

from podcast_scribe.public_storage import load_stored_record, record_bytes, storage_manifest, stored_files
from podcast_scribe.share import submission_digest
from test_public_site import record, builder_module


def test_small_record_is_readable_utf8_without_changing_content_or_digest():
    source = record(content="逐段核对中文正文。")
    before = deepcopy(source)
    files = dict(stored_files(source, 1))
    assert set(files) == {"issue-1.json"}
    payload = files["issue-1.json"]
    text = payload.decode("utf-8")
    assert text.startswith('{\n  "provenance": {\n    "issue_url": ')
    assert '\n          "text": "逐段核对中文正文。"\n' in text
    assert text.endswith("\n}\n") and not text.endswith("\n\n")
    assert "\\u" not in text
    loaded = load_stored_record(payload, 1, lambda *_: pytest.fail("Single JSON needs no parts"))
    assert loaded == before == source
    assert loaded["provenance"]["payload_sha256"] == submission_digest(before["submission"])


def test_record_output_is_stable_regardless_of_object_insertion_order():
    def reversed_objects(value):
        if isinstance(value, dict):
            return {key: reversed_objects(value[key]) for key in reversed(value)}
        if isinstance(value, list):
            return [reversed_objects(item) for item in value]
        return value

    source = record()
    assert dict(stored_files(source, 1)) == dict(stored_files(reversed_objects(source), 1))


@pytest.mark.parametrize("remaining_bytes", [0, -1])
def test_readable_byte_limit_includes_final_newline(monkeypatch, remaining_bytes):
    source = record(content="边界检查中文正文。")
    pretty = (json.dumps(source, ensure_ascii=False, sort_keys=True, indent=2,
                         allow_nan=False) + "\n").encode("utf-8")
    monkeypatch.setattr("podcast_scribe.public_storage.PART_BYTES", len(pretty) + remaining_bytes)
    files = dict(stored_files(source, 1))
    if remaining_bytes == 0:
        assert files == {"issue-1.json": pretty}
    else:
        manifest_payload = files["issue-1.json"]
        manifest = storage_manifest(manifest_payload, 1)
        assert manifest is not None
        assert len(manifest["parts"]) == 1
        assert files[manifest["parts"][0]["name"]] == record_bytes(source)
        assert manifest_payload.startswith(b'{\n  "byte_length": ')
        assert manifest_payload.endswith(b"\n}\n")
    assert load_stored_record(files["issue-1.json"], 1, lambda name, _: files[name]) == source


def test_near_total_limit_uses_compact_parts_when_pretty_record_would_grow(monkeypatch):
    source = record()
    compact = record_bytes(source)
    monkeypatch.setattr("podcast_scribe.public_storage.MAX_RECORD_BYTES", len(compact))
    files = dict(stored_files(source, 1))
    manifest = storage_manifest(files["issue-1.json"], 1)
    assert manifest is not None
    assert manifest["byte_length"] == len(compact)
    assert load_stored_record(files["issue-1.json"], 1, lambda name, _: files[name]) == source


def test_record_exceeding_total_limit_is_rejected(monkeypatch):
    source = record()
    monkeypatch.setattr("podcast_scribe.public_storage.MAX_RECORD_BYTES", len(record_bytes(source)) - 1)
    with pytest.raises(ValueError, match="too large"):
        list(stored_files(source, 1))


@pytest.mark.parametrize("chunked", [False, True])
def test_legacy_compact_record_and_manifest_remain_readable(chunked):
    source = record()
    compact = (json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
    assert compact.count(b"\n") == 1
    files = {}
    payload = compact
    if chunked:
        parts = []
        for index, offset in enumerate(range(0, len(compact), 512)):
            chunk = compact[offset:offset + 512]
            name = f"issue-1/part-{index:05d}.bin"
            files[name] = chunk
            parts.append({"name": name, "size": len(chunk), "sha256": hashlib.sha256(chunk).hexdigest()})
        manifest = {"schema_version": 2, "storage": "chunked-json", "byte_length": len(compact),
                    "sha256": hashlib.sha256(compact).hexdigest(), "parts": parts}
        payload = (json.dumps(manifest, separators=(",", ":")) + "\n").encode("utf-8")
        assert payload.count(b"\n") == 1
    assert load_stored_record(payload, 1, lambda name, _: files[name]) == source


def test_editing_one_segment_only_changes_its_text_and_digest_lines():
    before = dict(stored_files(record(content="原有正文。"), 1))["issue-1.json"].decode("utf-8")
    after = dict(stored_files(record(content="修正正文。"), 1))["issue-1.json"].decode("utf-8")
    changed = [line for line in difflib.unified_diff(before.splitlines(), after.splitlines())
               if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    assert len(changed) == 4
    assert all('"text":' in line or '"payload_sha256":' in line for line in changed)


def files_for(source, monkeypatch):
    monkeypatch.setattr("podcast_scribe.public_storage.PART_BYTES", 512)
    return dict(stored_files(source, 1))


def test_chunked_storage_roundtrip_is_bound_to_each_part_and_whole_record(monkeypatch):
    source = record(content="公开正文。" * 300)
    files = files_for(source, monkeypatch)
    manifest = storage_manifest(files["issue-1.json"], 1)
    assert len(manifest["parts"]) > 1
    assert load_stored_record(files["issue-1.json"], 1, lambda name, size: files[name]) == source
    manifest["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="record digest"):
        load_stored_record(json.dumps(manifest).encode(), 1, lambda name, size: files[name])


@pytest.mark.parametrize("change", ["traversal", "other_issue", "oversize", "missing", "digest", "duplicate"])
def test_invalid_storage_manifest_or_part_cannot_be_loaded(monkeypatch, change):
    files = files_for(record(content="正文" * 300), monkeypatch)
    manifest = json.loads(files["issue-1.json"])
    if change == "traversal": manifest["parts"][0]["name"] = "../private.json"
    if change == "other_issue": manifest["parts"][0]["name"] = "issue-2/part-00000.bin"
    if change == "oversize": manifest["parts"][0]["size"] = 1024
    if change == "missing": manifest["parts"].pop()
    if change == "digest": files[manifest["parts"][0]["name"]] = b"broken"
    if change == "duplicate": manifest["parts"][1]["name"] = manifest["parts"][0]["name"]
    with pytest.raises(ValueError):
        load_stored_record(json.dumps(manifest).encode(), 1, lambda name, size: files[name])


def test_builder_loads_manifest_and_rejects_symlink_parts(tmp_path, monkeypatch):
    source = record(content="正文" * 300)
    files = files_for(source, monkeypatch)
    for name, payload in files.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(payload)
    builder = builder_module()
    assert builder.load_records(tmp_path) == [source]
    part = next(path for path in (tmp_path / "issue-1").iterdir())
    part.unlink()
    part.symlink_to(tmp_path / "issue-1.json")
    with pytest.raises(ValueError, match="regular"):
        builder.load_records(tmp_path)
