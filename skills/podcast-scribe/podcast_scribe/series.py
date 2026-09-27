"""Local, explicit programme names and a maintainer-owned canonical catalogue.

Resolution never infers a programme from an uploader, episode title or body and
never fetches remote resources. The agent supplies a name verified from source
material; this module only resolves that name against the bundled catalogue.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from .model import ContentError


UNCATEGORIZED = {"id": "inbox", "title": "未分类", "description": ""}
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent / "assets" / "series-catalog.json"
MAX_CATALOG_BYTES = 1024 * 1024
_PLACEHOLDERS = frozenset({"未分类", "待分类", "待归类"})


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _text(value, label, *, maximum=300, empty=False):
    if (not isinstance(value, str) or len(value) > maximum
            or (not empty and not value.strip())):
        raise ContentError(f"{label} 必须是{'可空' if empty else '非空'}文本，最多 {maximum} 字符")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
        raise ContentError(f"{label} 包含不支持的控制字符")
    return value


def _identifier(value, label="series.id"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ContentError(f"{label} 必须是 1–100 位字母、数字、下划线或短横线")


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise ContentError(f"{label} 字段必须且只能为：{', '.join(sorted(fields))}")


def _url(value, label):
    _text(value, label, maximum=2048)
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"https", "http"} and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and "\\" not in value and not any(char.isspace() for char in value))
        parsed.port
    except ValueError as exc:
        raise ContentError(f"{label} 必须是有效的 http/https 链接") from exc
    if not valid:
        raise ContentError(f"{label} 必须是无账号密码的 http/https 链接")


def validate_evidence_url(value) -> str:
    """Validate a citation's URL syntax without fetching it or asserting trust."""
    _url(value, "系列依据链接")
    return value


def _validate_catalog(catalog):
    _object(catalog, {"schema_version", "series"}, "series catalog")
    if type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
        raise ContentError("系列目录需要 schema_version=1")
    rows = catalog["series"]
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ContentError("系列目录必须是最多 5000 项的列表")
    ids, names = set(), {}
    for row in rows:
        _object(row, {"id", "title", "description", "aliases", "sources"}, "catalog.series")
        _identifier(row["id"])
        if row["id"].casefold() == "inbox":
            raise ContentError("inbox 为未分类保留 ID，不能注册为正式系列")
        if row["id"] in ids:
            raise ContentError(f"系列 ID 重复：{row['id']}")
        ids.add(row["id"])
        _text(row["title"], "series.title")
        _text(row["description"], "series.description", maximum=20000, empty=True)
        if not isinstance(row["aliases"], list) or len(row["aliases"]) > 100:
            raise ContentError("series.aliases 必须是最多 100 项的列表")
        for name in [row["title"], *row["aliases"]]:
            _text(name, "系列名称/别名")
            key = _key(name)
            if key in _PLACEHOLDERS:
                raise ContentError("未分类、待分类和待归类为保留名称，不能注册为系列名称/别名")
            if key in names:
                raise ContentError(f"系列名称/别名重复或歧义：{name}（{names[key]} / {row['id']}）")
            names[key] = row["id"]
        if not isinstance(row["sources"], list) or not 1 <= len(row["sources"]) <= 20:
            raise ContentError("series.sources 必须是包含 1–20 项的来源列表")
        for source in row["sources"]:
            _object(source, {"title", "url"}, "series.source")
            _text(source["title"], "series.source.title")
            _url(source["url"], "series.source.url")
    return catalog


def validate_catalog(catalog) -> dict:
    """Validate a catalogue and return a detached copy, including all sources."""
    _validate_catalog(catalog)
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(catalog):
        size += len(chunk.encode("utf-8"))
        if size > MAX_CATALOG_BYTES:
            raise ContentError("系列目录不能超过 1 MiB")
    return deepcopy(catalog)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContentError(f"系列目录 JSON 字段重复：{key}")
        result[key] = value
    return result


def load_catalog(path=None) -> dict:
    """Load a bounded local JSON catalogue; reject ambiguity before resolving."""
    target = DEFAULT_CATALOG_PATH if path is None else Path(path)
    try:
        with target.open("rb") as handle:
            content = handle.read(MAX_CATALOG_BYTES + 1)
        if len(content) > MAX_CATALOG_BYTES:
            raise ContentError("系列目录不能超过 1 MiB")
        catalog = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ContentError):
            raise
        raise ContentError(f"无法读取系列目录 {target}：{exc}") from exc
    return validate_catalog(catalog)


def _public_series(row):
    return {field: row[field] for field in ("id", "title", "description")}


def _generated_id(title):
    # Including a digest prevents unrelated Chinese names with a shared Latin
    # fragment (e.g. "AI 访谈" and "AI 新闻") from collapsing onto the same ID.
    key = _key(title)
    slug = re.sub(r"[^a-z0-9_-]+", "-", key).strip("-_")[:64] or "series"
    return f"{slug}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}"


def resolve_series(*, series_id=None, series_title=None, catalog=None) -> dict:
    """Resolve an explicit ID/name/alias, or return an honest uncategorized slot.

    A known ID may stand alone. Unknown IDs require a title. When both arguments
    identify different programmes, fail instead of silently choosing one.
    """
    catalog = load_catalog() if catalog is None else validate_catalog(catalog)
    if series_id is not None:
        _identifier(series_id)
    if series_title is not None:
        _text(series_title, "series.title")
    title_key = _key(series_title) if series_title is not None else None
    placeholder_id = series_id is not None and series_id.casefold() == "inbox"
    placeholder_title = title_key in _PLACEHOLDERS
    if series_id is None and series_title is None:
        return dict(UNCATEGORIZED)
    if placeholder_id or placeholder_title:
        if (series_id is not None and not placeholder_id
                or series_title is not None and not placeholder_title):
            raise ContentError("未分类保留 ID/名称不能与正式系列 ID/名称混用")
        return dict(UNCATEGORIZED)
    by_id = {row["id"]: row for row in catalog["series"]}
    by_name = {_key(name): row for row in catalog["series"]
               for name in [row["title"], *row["aliases"]]}
    id_match = by_id.get(series_id)
    title_match = by_name.get(title_key)
    if id_match is not None:
        if series_title is not None and (title_match is None or title_match["id"] != id_match["id"]):
            raise ContentError("系列 ID 与名称不一致；请使用目录中的正式名称或别名")
        return _public_series(id_match)
    if title_match is not None:
        if series_id is not None:
            raise ContentError(f"系列名称已对应 ID {title_match['id']}，不能使用另一个 ID {series_id}")
        return _public_series(title_match)
    if series_title is None:
        raise ContentError("未知系列 ID 需要同时提供系列名称")
    final_id = series_id or _generated_id(series_title)
    if final_id in by_id:
        raise ContentError("生成的系列 ID 已存在，请显式指定新的系列 ID")
    return {"id": final_id, "title": series_title.strip(), "description": ""}


def display_series(series: dict) -> dict:
    """Hide obsolete placeholder wording without changing stored user metadata."""
    result = deepcopy(series)
    title = result.get("title")
    series_id = result.get("id")
    if ((series_id is None or isinstance(series_id, str) and series_id.casefold() == "inbox")
            and (title is None or isinstance(title, str) and _key(title) in _PLACEHOLDERS)):
        result.update(UNCATEGORIZED)
    return result
