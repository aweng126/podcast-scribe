#!/usr/bin/env python3
"""Build an explicitly synthetic, offline demonstration without calling any API."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from podcast_scribe.exporters import export_episode
from podcast_scribe.model import load_episode, save_episode
from podcast_scribe.site import build_site


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成自制演示文稿和本地阅读站，无需 API。")
    parser.add_argument("--output-dir", type=Path, default=Path("output/demo"),
                        help="相对于当前任务目录的演示输出路径，默认 output/demo")
    parser.add_argument("--formats", nargs="+", choices=["markdown", "pdf"], default=["markdown", "pdf"])
    args = parser.parse_args(argv)
    ep = load_episode(ROOT / "examples/demo/episode.json")
    paths = export_episode(ep, args.output_dir, args.formats)
    ep["artifacts"] = {kind: str(path.resolve()) for kind, path in paths.items()}
    save_episode(args.output_dir / "episode.json", ep)
    preview = build_site([ep], args.output_dir / "preview", include_drafts=True)
    print("自制演示，不是 B站示例的转写：")
    print(preview.resolve())
    for path in paths.values():
        print(path.resolve())


if __name__ == "__main__":
    main()
