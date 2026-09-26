#!/usr/bin/env python3
"""Build an explicitly synthetic, offline demonstration without calling any API."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from video_to_markdown.exporters import export_episode
from video_to_markdown.model import load_episode, save_episode
from video_to_markdown.site import build_site


def main():
    ep = load_episode(ROOT / "examples/demo/episode.json")
    paths = export_episode(ep, ROOT / "output/demo", ["markdown", "pdf"])
    ep["artifacts"] = {kind: str(path.resolve()) for kind, path in paths.items()}
    save_episode(ROOT / "output/demo/episode.json", ep)
    preview = build_site([ep], ROOT / "output/demo/preview", include_drafts=True)
    print("自制演示，不是 B站示例的转写：")
    print(preview)
    for path in paths.values():
        print(path)


if __name__ == "__main__":
    main()
