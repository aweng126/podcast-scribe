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

from .exporters import _md, render_markdown
from .reading import reading_turns, segment_text_parts
from .share import MAX_SUBMISSION_BYTES, submission_digest, submission_episode, validate_submission


REPOSITORY_URL = "https://github.com/aweng126/podcast-scribe"
SUBMIT_URL = REPOSITORY_URL + "/issues/new?template=share.yml"
_ASSETS = Path(__file__).with_name("assets")
_ISSUE = re.compile(re.escape(REPOSITORY_URL) + r"/issues/([1-9][0-9]*)\Z")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:\[bot\])?\Z")
_OWNED = re.compile(r"(?:episodes/[0-9a-f]{64}\.json|downloads/[0-9a-f]{64}\.md)\Z")


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


def build_public_site(records: list[dict], out_dir: Path) -> Path:
    """Validate the complete approved library, then build a static Pages site.

    No input can name an output path or a local artifact. All downloads are
    rendered afresh. Rebuilds remove only files owned by the previous manifest.
    Invalid input leaves an existing site unchanged.
    """
    checked = [validate_record(record) for record in records]
    for field, values in (
        ("episode IDs", [item["submission"]["episode"]["id"] for item in checked]),
        ("source Issues", [item["provenance"]["issue_url"] for item in checked]),
        ("submission digests", [item["provenance"]["payload_sha256"] for item in checked]),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"Public library {field} must be unique.")
    checked.sort(key=lambda item: item["submission"]["episode"]["id"])
    files = {}
    catalogue = []
    search = []
    for record in checked:
        identifier = record["submission"]["episode"]["id"]
        filename = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        public = _episode(record, filename)
        catalogue.append(_metadata(public, filename))
        files[f"episodes/{filename}.json"] = _json(public)
        markdown = render_markdown(submission_episode(record["submission"]))
        markdown += (
            "\n## 投稿信息\n\n"
            + "- **投稿署名**：" + _md(record["submission"]["attribution"]) + "\n"
            + "- **投稿账号**：" + _md(record["provenance"]["submitter"]) + "\n"
            + "- **投稿记录**：[GitHub Issue](" + record["provenance"]["issue_url"] + ")\n"
        )
        files[f"downloads/{filename}.md"] = markdown
        search.append({"id": identifier, "text": " ".join([
            public["title"], public["description"], public["series"]["title"],
            *public["summary"], *(speaker["name"] for speaker in public["speakers"]),
            *(turn["text"] for turn in public["turns"]),
        ])})
    data = {"schema_version": 1, "mode": "public", "preview": False,
            "repository_url": REPOSITORY_URL, "submit_url": SUBMIT_URL,
            "search_url": "search-index.json", "episodes": catalogue}
    serialized = _json(data).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (_ASSETS / "index.html").read_text(encoding="utf-8")
    files["index.html"] = template.replace("<!-- TRANSCRIPT_DATA -->", serialized)
    files["search-index.json"] = _json({"schema_version": 1, "episodes": search})
    for name in ("app.js", "app.css"):
        files[name] = (_ASSETS / name).read_text(encoding="utf-8")
    files[".nojekyll"] = ""
    # Resolve parent aliases such as macOS /tmp without following a symlink at
    # the user-selected output directory itself.
    out_dir = Path(out_dir).absolute()
    if out_dir.is_symlink():
        raise ValueError("Output directory must not be a symlink.")
    out_dir = out_dir.parent.resolve() / out_dir.name
    manifest = ".public-site-manifest.json"
    _check_output_path(out_dir, manifest)
    try:
        previous = json.loads((out_dir / manifest).read_text(encoding="utf-8")).get("files", [])
    except (OSError, ValueError, AttributeError):
        previous = []
    owned = sorted(name for name in files if _OWNED.fullmatch(name))
    stale = [name for name in previous if isinstance(name, str) and _OWNED.fullmatch(name) and name not in owned] if isinstance(previous, list) else []
    files[manifest] = _json({"files": owned})
    for name in [*files, *stale]:
        _check_output_path(out_dir, name)
    # Render and validate every document before changing the previous output.
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        if name == manifest:
            continue
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for name in stale:
        (out_dir / name).unlink(missing_ok=True)
    (out_dir / manifest).write_text(files[manifest], encoding="utf-8")
    return out_dir / "index.html"
