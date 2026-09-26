#!/usr/bin/env python3
"""Build the approved community library without installing dependencies."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "podcast-scribe"))

from podcast_scribe.public_site import build_public_site, loads_record  # noqa: E402
from podcast_scribe.share import MAX_SUBMISSION_BYTES  # noqa: E402


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
            record = loads_record(stream.read(MAX_SUBMISSION_BYTES + 4097))
        if record["provenance"]["issue_url"].rsplit("/", 1)[1] != match[1]:
            raise ValueError(f"Public record filename must match its source Issue: {path.name}")
        records.append(record)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-dir", type=Path, default=ROOT / "content" / "episodes")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "site")
    args = parser.parse_args(argv)
    try:
        target = build_public_site(load_records(args.content_dir), args.output_dir)
    except (OSError, ValueError) as error:
        print(f"Public site build failed: {error}", file=sys.stderr)
        return 2
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
