"""Large content remains digest-bound through chunked Git storage."""
import json
import pytest

from podcast_scribe.public_storage import load_stored_record, storage_manifest, stored_files
from test_public_site import record, builder_module


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
