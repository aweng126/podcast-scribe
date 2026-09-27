"""Series names are explicit, source-backed and deterministic across episodes."""
from copy import deepcopy
import json
import socket

import pytest

from podcast_scribe.model import ContentError
from podcast_scribe.series import (
    MAX_CATALOG_BYTES, UNCATEGORIZED, display_series, load_catalog, resolve_series,
    validate_catalog, validate_evidence_url,
)


@pytest.fixture
def catalog():
    return {"schema_version": 1, "series": [
        {"id": "small-interviews", "title": "小型访谈", "description": "节目介绍。",
         "aliases": ["Small Interviews", "小型访谈播客"],
         "sources": [{"title": "节目主页", "url": "https://example.org/podcast"}]},
        {"id": "other-show", "title": "另一节目", "description": "", "aliases": [],
         "sources": [{"title": "节目官网", "url": "https://example.org/other"}]},
    ]}


def test_bundled_catalog_loads_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("classification must not open a network connection")
    monkeypatch.setattr(socket, "socket", forbidden)
    data = load_catalog()
    assert data["schema_version"] == 1
    assert isinstance(data["series"], list)
    assert resolve_series() == UNCATEGORIZED
    assert resolve_series(series_title="未收录的访谈")["title"] == "未收录的访谈"


def test_catalog_loader_preserves_sources_and_returns_independent_objects(tmp_path, catalog):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    loaded = load_catalog(path)
    assert loaded == catalog
    loaded["series"][0]["title"] = "changed"
    assert load_catalog(path) == catalog


def test_validate_catalog_returns_independent_copy(catalog):
    original = deepcopy(catalog)
    validated = validate_catalog(catalog)
    validated["series"][0]["sources"][0]["title"] = "changed"
    assert catalog == original


def test_evidence_url_validation_is_local_and_preserves_link(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no network required for URL syntax validation")
    monkeypatch.setattr(socket, "socket", forbidden)
    assert validate_evidence_url("https://example.org/source?q=1#evidence") == "https://example.org/source?q=1#evidence"
    with pytest.raises(ContentError):
        validate_evidence_url("https://secret@example.org")


@pytest.mark.parametrize("kwargs", [
    {"series_id": "small-interviews"},
    {"series_title": "小型访谈"},
    {"series_title": "小型访谈播客"},
    {"series_title": "  ＳＭＡＬＬ  Interviews  "},
    {"series_id": "small-interviews", "series_title": "Small Interviews"},
])
def test_exact_names_and_aliases_reuse_canonical_series(catalog, kwargs):
    original = deepcopy(catalog)
    assert resolve_series(catalog=catalog, **kwargs) == {
        "id": "small-interviews", "title": "小型访谈", "description": "节目介绍。"}
    assert catalog == original


@pytest.mark.parametrize("kwargs", [
    {"series_id": "small-interviews", "series_title": "另一节目"},
    {"series_id": "small-interviews", "series_title": "未收录名称"},
    {"series_id": "different-id", "series_title": "Small Interviews"},
    {"series_id": "different-id"},
    {"series_id": "inbox", "series_title": "小型访谈"},
    {"series_id": "small-interviews", "series_title": "未分类"},
])
def test_conflicting_arguments_and_unknown_id_alone_fail(catalog, kwargs):
    with pytest.raises(ContentError):
        resolve_series(catalog=catalog, **kwargs)


@pytest.mark.parametrize("kwargs", [{}, {"series_id": "inbox"}, {"series_title": "待归类"},
                                     {"series_id": "inbox", "series_title": "未分类"}])
def test_missing_information_stays_uncategorized(catalog, kwargs):
    resolved = resolve_series(catalog=catalog, **kwargs)
    assert resolved == UNCATEGORIZED
    resolved["title"] = "changed"
    assert UNCATEGORIZED["title"] == "未分类"


def test_unknown_titles_have_stable_safe_distinct_ids(catalog):
    chinese = resolve_series(series_title="未收录的访谈", catalog=catalog)
    assert chinese == resolve_series(series_title="未收录的访谈", catalog=catalog)
    assert chinese["id"].isascii() and "/" not in chinese["id"]
    first = resolve_series(series_title="ＡＩ  访谈", catalog=catalog)
    assert first["id"] == resolve_series(series_title="ai 访谈", catalog=catalog)["id"]
    assert first["id"] != resolve_series(series_title="AI 新闻", catalog=catalog)["id"]
    assert len(resolve_series(series_title="x" * 300, catalog=catalog)["id"]) <= 100


def test_explicit_new_series_id_is_kept_and_never_matches_substrings(catalog):
    assert resolve_series(series_id="my-show", series_title="新节目", catalog=catalog) == {
        "id": "my-show", "title": "新节目", "description": ""}
    assert resolve_series(series_title="小型访谈精选片段", catalog=catalog)["id"] != "small-interviews"


@pytest.mark.parametrize("series_id", ["../escape", "a/b", "", 12, "a" * 101, "a\n"])
def test_unsafe_explicit_ids_are_rejected(catalog, series_id):
    with pytest.raises(ContentError):
        resolve_series(series_id=series_id, series_title="新节目", catalog=catalog)


@pytest.mark.parametrize("title", ["", " ", [], 12, "A\nB", "A\x00B", "A\u202eB", "x" * 301])
def test_bad_titles_are_rejected(catalog, title):
    with pytest.raises(ContentError):
        resolve_series(series_title=title, catalog=catalog)


@pytest.mark.parametrize("change", [
    lambda c: c.update(schema_version=True),
    lambda c: c.update(extra="unknown"),
    lambda c: c["series"][0].update(extra="unknown"),
    lambda c: c["series"][0].update(id="../unsafe"),
    lambda c: c["series"][0].update(id="inbox"),
    lambda c: c["series"][0].update(id="Inbox"),
    lambda c: c["series"][1].update(id="small-interviews"),
    lambda c: c["series"][1].update(title="小型访谈"),
    lambda c: c["series"][1].update(title="ＳＭＡＬＬ  Interviews"),
    lambda c: c["series"][1].update(aliases=["Small interviews"]),
    lambda c: c["series"][0].update(aliases=["小型访谈"]),
    lambda c: c["series"][0].update(aliases=["same", "ＳＡＭＥ"]),
    lambda c: c["series"][0].update(aliases=["待分类"]),
    lambda c: c["series"][0].update(title="未分类"),
    lambda c: c["series"][0].update(description="包含\x7f控制字符"),
    lambda c: c["series"][0].update(aliases="wrong type"),
    lambda c: c["series"][0].update(sources=[]),
    lambda c: c["series"][0].update(sources=[{"url": "https://example.org"}]),
])
def test_catalog_schema_ids_and_name_ambiguities_are_rejected(tmp_path, catalog, change):
    change(catalog)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(ContentError):
        load_catalog(path)
    with pytest.raises(ContentError):
        resolve_series(catalog=catalog)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "https://user:secret@example.org/",
    "https://user@example.org/", "https:///missing", "https://example.org:bad/",
    "https://example.org:70000/", "https://example.org/has space", "https://example.org\\path",
    "https://example.org/\npath", "https://[invalid/",
])
def test_catalog_rejects_nonpublic_url_syntax(catalog, url):
    catalog["series"][0]["sources"][0]["url"] = url
    with pytest.raises(ContentError):
        resolve_series(catalog=catalog)


@pytest.mark.parametrize("content", [b"not JSON", b"\xff", b'{"schema_version":1,"schema_version":1,"series":[]}'])
def test_catalog_rejects_invalid_encoding_json_and_duplicate_keys(tmp_path, content):
    path = tmp_path / "catalog.json"
    path.write_bytes(content)
    with pytest.raises(ContentError):
        load_catalog(path)


def test_catalog_size_and_missing_file_errors_are_bounded(tmp_path):
    with pytest.raises(ContentError):
        load_catalog(tmp_path / "missing.json")
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (MAX_CATALOG_BYTES + 1))
    with pytest.raises(ContentError, match="1 MiB"):
        load_catalog(path)


@pytest.mark.parametrize("title", ["待归类", "待分类", "未分类"])
def test_display_legacy_placeholder_without_mutation(title):
    original = {"id": "inbox", "title": title, "description": ""}
    before = deepcopy(original)
    assert display_series(original) == UNCATEGORIZED
    assert original == before


@pytest.mark.parametrize("series", [
    {"id": "inbox", "title": "明确指定的节目", "description": "说明"},
    {"id": "custom", "title": "待归类", "description": "说明"},
    {"id": "custom", "title": "自定义节目", "description": "说明"},
])
def test_display_preserves_explicit_custom_metadata(series):
    copy = display_series(series)
    assert copy == series
    copy["title"] = "edited"
    assert copy != series
