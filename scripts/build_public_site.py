#!/usr/bin/env python3
"""Build the approved community library without installing dependencies."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "podcast-scribe"))

from podcast_scribe.public_site import (  # noqa: E402
    _apply_series, build_public_site, load_series_overrides, validate_record,
    validate_series_overrides,
)
from podcast_scribe.series import load_catalog, validate_catalog  # noqa: E402
from podcast_scribe.share import MAX_SUBMISSION_BYTES  # noqa: E402
from podcast_scribe.public_storage import load_stored_record  # noqa: E402


def load_records(directory: Path) -> list[dict]:
    """Read only the frozen issue-N records from the approved content folder."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Content directory must exist and must not be a symlink.")
    records = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink():
            raise ValueError(f"Content entries must not be symlinks: {path.name}")
        if path.suffix != ".json":
            continue
        match = re.fullmatch(r"issue-([1-9][0-9]*)\.json", path.name)
        if not match or not path.is_file():
            raise ValueError(f"Unexpected public record filename: {path.name}")
        if path.stat().st_size > MAX_SUBMISSION_BYTES + 4096:
            raise ValueError(f"Public record is too large: {path.name}")
        with path.open("rb") as stream:
            payload = stream.read(MAX_SUBMISSION_BYTES + 4097)
        def read_part(name, size):
            target = directory / name
            if target.parent.is_symlink() or target.is_symlink() or not target.is_file():
                raise ValueError(f"Public part must be a regular file: {name}")
            with target.open("rb") as stream:
                return stream.read(size + 1)
        record = load_stored_record(payload, int(match[1]), read_part)
        if record["provenance"]["issue_url"].rsplit("/", 1)[1] != match[1]:
            raise ValueError(f"Public record filename must match its source Issue: {path.name}")
        records.append(record)
    return records


def replacement_report(records: list[dict], old_issue: int, new_issue: int,
                       catalog: dict, overrides: dict) -> dict:
    """Compare two explicitly selected Issues without replacing or reclassifying either."""
    if old_issue <= 0 or new_issue <= 0 or old_issue == new_issue:
        raise ValueError("Replacement requires two different positive Issue numbers.")
    catalog = validate_catalog(catalog)
    overrides = validate_series_overrides(overrides, catalog)
    selected = {}
    for source in records:
        number = int(source["provenance"]["issue_url"].rsplit("/", 1)[1])
        if number not in (old_issue, new_issue):
            continue
        if number in selected:
            raise ValueError(f"Duplicate source Issue #{number}.")
        record = validate_record(source)
        original = record["submission"]["episode"]
        # Use the exact display rules used by the site, on a detached metadata
        # copy. A replacement report never edits the frozen submission or hash.
        display = {"references": []}
        _apply_series(display, record, catalog, overrides)
        selected[number] = {
            "issue": number, "episode_id": original["id"], "title": original["title"],
            "source_url": original["source"]["url"],
            "original_series": deepcopy(original["series"]),
            "effective_series": display["series"],
            "override": deepcopy(overrides["issues"].get(str(number))),
        }
    for number in (old_issue, new_issue):
        if number not in selected:
            raise ValueError(f"Issue #{number} is missing; run this check before removing the old record.")
    old, new = selected[old_issue], selected[new_issue]
    changed = old["effective_series"] != new["effective_series"]
    same_id = old["episode_id"] == new["episode_id"]
    same_source = old["source_url"] == new["source_url"]
    if new["override"]:
        action = "verify_new_override"
        message = "新 Issue 已有分类覆盖；请核实目标系列与依据链接，旧 Issue 的覆盖不会自动迁移。"
    elif old["override"]:
        action = "review_old_override"
        message = ("旧 Issue 的分类覆盖不会随正文替换迁移。请核实新稿归属；"
                   "若仍需该覆盖，在新 Issue 编号下添加适用的系列与依据链接。")
    elif changed:
        action = "verify_series_change"
        message = "替换后的有效分类发生变化，请核实新稿系列；必要时为新 Issue 添加分类覆盖。"
    else:
        action = "none"
        message = "有效分类一致，且没有需要迁移的旧 Issue 分类覆盖。"
    warnings = []
    if not same_id:
        warnings.append("新旧单集 ID 不同，请先确认这是同一单集的修订稿。")
    if not same_source:
        warnings.append("新旧来源链接不同，请先核实来源；工具不据此认定为同一单集。")
    return {
        "schema_version": 1, "old": old, "new": new,
        "same_episode_id": same_id, "same_source_url": same_source,
        "effective_series_changed": changed,
        "requires_series_review": action != "none",
        "override_action": action, "message": message, "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-dir", type=Path, default=ROOT / "content" / "episodes")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "site")
    parser.add_argument("--series-catalog", type=Path, help="Optional maintained series catalog JSON")
    parser.add_argument("--series-overrides", type=Path, default=ROOT / "content" / "series-overrides.json")
    parser.add_argument("--check-replacement", type=int, nargs=2, metavar=("OLD_ISSUE", "NEW_ISSUE"),
                        help="Only report classification before/after an Issue replacement; write nothing")
    args = parser.parse_args(argv)
    try:
        records = load_records(args.content_dir)
        catalog = load_catalog(args.series_catalog)
        overrides = load_series_overrides(args.series_overrides)
        if args.check_replacement:
            report = replacement_report(records, *args.check_replacement, catalog, overrides)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        target = build_public_site(records, args.output_dir,
                                   series_catalog=catalog, series_overrides=overrides)
    except (OSError, ValueError) as error:
        operation = "Replacement check" if args.check_replacement else "Public site build"
        print(f"{operation} failed: {error}", file=sys.stderr)
        return 2
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
