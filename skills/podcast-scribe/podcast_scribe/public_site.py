"""Build a hosted reading library from reviewed, public submission records.

The HTML contains only catalogue metadata. Full episodes and the full-text
search index are separate relative resources, loaded by the reader on demand.
Local offline exports continue to use :mod:`podcast_scribe.site`.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

from .exporters import _md, render_markdown
from .reading import reading_turns, segment_text_parts
from .series import (UNCATEGORIZED, display_series, load_catalog, resolve_series,
                     validate_catalog, validate_evidence_url)
from .share import MAX_SUBMISSION_BYTES, submission_digest, submission_episode, validate_submission


REPOSITORY_URL = "https://github.com/aweng126/podcast-scribe"
SUBMIT_URL = REPOSITORY_URL + "/issues/new?template=share.yml"
_ASSETS = Path(__file__).with_name("assets")
_ISSUE = re.compile(re.escape(REPOSITORY_URL) + r"/issues/([1-9][0-9]*)\Z")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:\[bot\])?\Z")
_OWNED = re.compile(r"(?:episodes/[0-9a-f]{64}(?:-[0-9]{5})?\.json|downloads/[0-9a-f]{64}\.md)\Z")
PAGED_TEXT_BYTES = 1024 * 1024
PAGE_TEXT_BYTES = 256 * 1024
MAX_SITE_BYTES = 1_000_000_000
MAX_SERIES_OVERRIDES_BYTES = 1024 * 1024


def load_series_overrides(path: Path) -> dict:
    """Load a bounded maintainer file, rejecting duplicate keys and symlinks."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Series overrides must be a regular JSON file.")
    with path.open("rb") as stream:
        payload = stream.read(MAX_SERIES_OVERRIDES_BYTES + 1)
    if len(payload) > MAX_SERIES_OVERRIDES_BYTES:
        raise ValueError("Series overrides exceed 1 MiB.")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Series overrides have a duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"Series overrides have a non-finite JSON value: {value}")

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("Invalid series overrides JSON.") from error


def validate_series_overrides(value: object, catalog: dict) -> dict:
    """Validate issue-specific display choices without editing frozen records.

    A removed Issue may retain an otherwise valid mapping, so withdrawing a
    transcript never depends on cleaning up this file in the same commit.
    """
    if (not isinstance(value, dict) or set(value) != {"schema_version", "issues"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["issues"], dict) or len(value["issues"]) > 10000):
        raise ValueError("Series overrides require schema_version=1 and an issues object.")
    known = {"inbox", *(item["id"] for item in catalog["series"])}
    for number, override in value["issues"].items():
        if not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]*", number):
            raise ValueError("Series override keys must be positive Issue numbers.")
        if not isinstance(override, dict) or set(override) != {"series_id", "evidence_url"}:
            raise ValueError("Each series override requires only series_id and evidence_url.")
        if not isinstance(override["series_id"], str) or override["series_id"] not in known:
            raise ValueError("Series override series_id must exist in the series catalog.")
        validate_evidence_url(override["evidence_url"])
    return deepcopy(value)


def _apply_series(episode: dict, record: dict, catalog: dict, overrides: dict) -> None:
    """Apply classification only to an isolated renderer episode."""
    number = record["provenance"]["issue_url"].rsplit("/", 1)[1]
    override = overrides["issues"].get(number)
    original = record["submission"]["episode"]["series"]
    series_id = override["series_id"] if override else original["id"]
    canonical = (UNCATEGORIZED if override and series_id == "inbox" else
                 next((item for item in catalog["series"] if item["id"] == series_id), original))
    if not override and canonical is not original:
        try:
            canonical = resolve_series(series_id=series_id, series_title=original["title"], catalog=catalog)
        except ValueError as error:
            raise ValueError(f"Issue #{number} series ID matches the catalog but its title does not; "
                             "confirm the title alias or add an explicit Issue series override.") from error
    episode["series"] = display_series({key: canonical[key] for key in ("id", "title", "description")})
    if override:
        episode["references"] = [*episode["references"], {
            "title": "节目系列来源（维护者确认）",
            "url": override["evidence_url"],
        }]


def validate_record(record: object) -> dict:
    """Return a detached, strict public record, including immutable provenance."""
    if not isinstance(record, dict) or set(record) != {"schema_version", "submission", "provenance"}:
        raise ValueError("Public record must contain only schema_version, submission and provenance.")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("Unsupported public record schema_version.")
    submission = validate_submission(record["submission"])
    provenance = record["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {"issue_url", "submitter", "payload_sha256"}:
        raise ValueError("Public record provenance must contain issue_url, submitter and payload_sha256.")
    if not isinstance(provenance["issue_url"], str) or not _ISSUE.fullmatch(provenance["issue_url"]):
        raise ValueError("Public record issue_url must identify a repository Issue.")
    if not isinstance(provenance["submitter"], str) or not _LOGIN.fullmatch(provenance["submitter"]):
        raise ValueError("Public record submitter must be a GitHub login.")
    if provenance["payload_sha256"] != submission_digest(submission):
        raise ValueError("Public record payload_sha256 does not match the submission.")
    return {"schema_version": 1, "submission": deepcopy(submission), "provenance": deepcopy(provenance)}


def loads_record(payload: bytes) -> dict:
    """Parse bounded JSON without duplicate keys or non-finite constants."""
    if not isinstance(payload, bytes) or len(payload) > MAX_SUBMISSION_BYTES + 4096:
        raise ValueError("Public record must be bounded UTF-8 JSON.")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Public record has a duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"Public record has a non-finite JSON value: {value}")

    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("Invalid public record JSON.") from error
    return validate_record(data)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def _episode(record: dict, filename: str) -> dict:
    public = deepcopy(record["submission"]["episode"])
    public.update(
        status="published", is_demo=False,
        attribution=record["submission"]["attribution"],
        provenance=deepcopy(record["provenance"]),
        downloads={"markdown": f"downloads/{filename}.md"},
    )
    public["turns"] = [
        {
            "speaker_id": turn["speaker_id"], "start": turn["start"], "end": turn["end"],
            "segment_indices": [segment["index"] for segment in turn["segments"]],
            "text": turn["text"], "text_parts": segment_text_parts(turn["segments"]),
            "needs_review": turn["needs_review"],
        }
        for turn in reading_turns(public)
    ]
    return public


def _metadata(episode: dict, filename: str) -> dict:
    return {
        "id": episode["id"], "title": episode["title"],
        "description": episode["description"][:280],
        "series": {**episode["series"], "description": episode["series"]["description"][:280]},
        "duration_seconds": episode["duration_seconds"], "published_at": episode["published_at"],
        "speakers": [{"id": item["id"], "name": item["name"]} for item in episode["speakers"]],
        "summary": [item[:280] for item in episode["summary"][:1]],
        "status": "published", "is_demo": False, "data_url": f"episodes/{filename}.json",
    }


def _paged_episode(record: dict, filename: str, emit) -> dict:
    source = record["submission"]["episode"]
    public = {key: value for key, value in source.items() if key != "segments"}
    public.update(status="published", is_demo=False, attribution=record["submission"]["attribution"],
                  provenance=record["provenance"], downloads={}, paginated=True, segments=[], turns=[],
                  segment_count=len(source["segments"]), pages=[], chapters=deepcopy(source["chapters"]))
    chunk, size, offset = [], 0, 0
    page_for_segment = {}
    def flush():
        nonlocal chunk, size, offset
        if not chunk:
            return
        number = len(public["pages"])
        url = f"episodes/{filename}-{number:05d}.json"
        emit(url, _json({"segments": chunk}))
        public["pages"].append({"url": url, "start": chunk[0]["start"], "end": chunk[-1]["end"],
                                "segment_start": offset, "segment_count": len(chunk)})
        page_for_segment.update((segment["id"], (number, offset + index)) for index, segment in enumerate(chunk))
        offset += len(chunk)
        chunk, size = [], 0
    for segment in source["segments"]:
        text_size = len(segment["text"].encode("utf-8"))
        if chunk and (size + text_size > PAGE_TEXT_BYTES or len(chunk) >= 200):
            flush()
        chunk.append(segment)
        size += text_size
    flush()
    for chapter in public["chapters"]:
        chapter["page"], chapter["segment_index"] = page_for_segment[chapter["segment_id"]]
    public["text_bytes"] = sum(len(segment["text"].encode("utf-8")) for segment in source["segments"])
    return public


def _check_output_path(out_dir: Path, relative: str) -> None:
    """Reject symlinks before writes or removal, including generated parents."""
    for path in [out_dir, *out_dir.parents]:
        if path.is_symlink():
            # macOS /tmp is a system alias, so callers should resolve only the
            # already-existing parent before choosing their build directory.
            raise ValueError(f"Output path must not contain a symlink: {path}")
    candidate = out_dir
    for part in Path(relative).parts:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError(f"Generated output must not be a symlink: {candidate}")
    if candidate.exists() and not candidate.is_file():
        raise ValueError(f"Generated output must be a file: {candidate}")


def build_public_site(records: list[dict], out_dir: Path, *, series_catalog: dict | None = None,
                      series_overrides: dict | None = None) -> Path:
    """Validate the complete approved library, then build a static Pages site.

    No input can name an output path or a local artifact. All downloads are
    rendered afresh. Rebuilds remove only files owned by the previous manifest.
    Invalid input leaves an existing site unchanged.
    """
    catalog = load_catalog() if series_catalog is None else validate_catalog(series_catalog)
    overrides = validate_series_overrides(
        {"schema_version": 1, "issues": {}} if series_overrides is None else series_overrides, catalog)
    checked = [validate_record(record) for record in records]
    for field, values in (
        ("episode IDs", [item["submission"]["episode"]["id"] for item in checked]),
        ("source Issues", [item["provenance"]["issue_url"] for item in checked]),
        ("submission digests", [item["provenance"]["payload_sha256"] for item in checked]),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"Public library {field} must be unique.")
    checked.sort(key=lambda item: item["submission"]["episode"]["id"])
    with tempfile.TemporaryDirectory(prefix="podcast-scribe-public-") as temporary:
        return _build_checked_site(checked, out_dir, Path(temporary), catalog, overrides)


def _build_checked_site(checked, out_dir, staging, series_catalog, series_overrides):
    files = []
    total = 0
    def emit(name, content):
        nonlocal total
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        total += target.stat().st_size
        if total > MAX_SITE_BYTES:
            raise ValueError("Public site exceeds the GitHub Pages 1 GB limit; reduce the library or use another host.")
        files.append(name)

    catalogue, search = [], []
    series_titles = {}
    paginated_count = 0
    for record in checked:
        identifier = record["submission"]["episode"]["id"]
        filename = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        text_bytes = sum(len(segment["text"].encode("utf-8")) for segment in record["submission"]["episode"]["segments"])
        paginated = text_bytes > PAGED_TEXT_BYTES
        public = _paged_episode(record, filename, emit) if paginated else _episode(record, filename)
        _apply_series(public, record, series_catalog, series_overrides)
        series_id, series_title = public["series"]["id"], public["series"]["title"]
        if series_id in series_titles and series_titles[series_id] != series_title:
            raise ValueError(f"Public series ID {series_id!r} has conflicting titles; resolve its Issue mappings before publication.")
        series_titles[series_id] = series_title
        paginated_count += int(paginated)
        catalogue.append(_metadata(public, filename))
        emit(f"episodes/{filename}.json", _json(public))
        if not paginated:
            renderer_episode = submission_episode(record["submission"])
            _apply_series(renderer_episode, record, series_catalog, series_overrides)
            markdown = render_markdown(renderer_episode)
            markdown += (
                "\n## 投稿信息\n\n"
                + "- **投稿署名**：" + _md(record["submission"]["attribution"]) + "\n"
                + "- **投稿账号**：" + _md(record["provenance"]["submitter"]) + "\n"
                + "- **投稿记录**：[GitHub Issue](" + record["provenance"]["issue_url"] + ")\n"
            )
            emit(f"downloads/{filename}.md", markdown)
        search.append({"id": identifier, "text": " ".join([
            public["title"], public["description"], public["series"]["title"],
            *public["summary"], *(speaker["name"] for speaker in public["speakers"]),
            *(turn["text"] for turn in public["turns"]),
        ])})
    data = {"schema_version": 1, "mode": "public", "preview": False,
            "repository_url": REPOSITORY_URL, "submit_url": SUBMIT_URL,
            "search_url": "search-index.json", "episodes": catalogue,
            "paginated_episodes": paginated_count}
    serialized = _json(data).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (_ASSETS / "index.html").read_text(encoding="utf-8")
    emit("index.html", template.replace("<!-- TRANSCRIPT_DATA -->", serialized))
    emit("search-index.json", _json({"schema_version": 1, "episodes": search}))
    emit("series-catalog.json", _json(series_catalog))
    for name in ("app.js", "app.css"):
        emit(name, (_ASSETS / name).read_text(encoding="utf-8"))
    emit(".nojekyll", "")
    out_dir = Path(out_dir).absolute()
    if out_dir.is_symlink():
        raise ValueError("Output directory must not be a symlink.")
    out_dir = out_dir.parent.resolve() / out_dir.name
    manifest = ".public-site-manifest.json"
    _check_output_path(out_dir, manifest)
    previous = []
    if (out_dir / manifest).exists():
        try:
            previous_manifest = json.loads((out_dir / manifest).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("Existing public site manifest is invalid; refusing to lose withdrawal tracking.") from error
        if (not isinstance(previous_manifest, dict) or set(previous_manifest) != {"files"}
                or not isinstance(previous_manifest["files"], list)
                or any(not isinstance(name, str) or not _OWNED.fullmatch(name) for name in previous_manifest["files"])
                or len(previous_manifest["files"]) != len(set(previous_manifest["files"]))):
            raise ValueError("Existing public site manifest is invalid; refusing to lose withdrawal tracking.")
        previous = previous_manifest["files"]
    owned = sorted(name for name in files if _OWNED.fullmatch(name))
    stale = [name for name in previous if name not in owned]
    emit(manifest, _json({"files": owned}))
    for name in [*files, *stale]:
        _check_output_path(out_dir, name)
    # All generation, size and path checks finish before modifying old output.
    # Only the current page is materialized at once, even for a 512 MiB input.
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in files:
        if name == manifest:
            continue
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staging / name, target)
    for name in stale:
        (out_dir / name).unlink(missing_ok=True)
    shutil.copyfile(staging / manifest, out_dir / manifest)
    return out_dir / "index.html"
