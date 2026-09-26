"""Local production CLI. Publishing marks content; it does not deploy a website."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from .model import ContentError, apply_edits, load_episode, new_episode, safe_id, save_episode, utc_now, validate_episode, write_json


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _series(parser):
    parser.add_argument("--series-id", default="inbox")
    parser.add_argument("--series-title", default="待归类")


def _output(parser):
    parser.add_argument("--output", required=True, type=Path, help="单集 JSON 文件路径；存在时不会覆盖")


def _new_destination(path):
    if path.exists():
        raise ContentError(f"文件已存在，已保留人工修改：{path}。请使用 edit/export 或指定新的输出路径。")


def _save_new(args, metadata, segments, speakers):
    ep = new_episode(metadata, segments, speakers, series_id=args.series_id, series_title=args.series_title)
    if getattr(args, "demo", False):
        ep["is_demo"] = True
    save_episode(args.output, ep)
    print(args.output.resolve())


def parser():
    root = argparse.ArgumentParser(prog="podcast-scribe", description="听稿 / Podcast Scribe：将播客与访谈整理成可阅读、可检索、可导出的完整文稿。")
    sub = root.add_subparsers(dest="command", required=True)
    from .doctor import CAPABILITIES
    p = sub.add_parser("doctor", help="按能力检查本地环境，不联网、不显示密钥")
    p.add_argument("--require", nargs="+", choices=CAPABILITIES,
                   help="只检查指定能力；省略时检查全部，缺少任一前置条件时退出码为 2")
    p = sub.add_parser("inspect", help="只读取 B站单集元数据")
    p.add_argument("url")
    p.add_argument("--output", type=Path)
    p = sub.add_parser("ingest", help="获取 B站音频并调用云端说话人转写")
    p.add_argument("url")
    p.add_argument("--cache", type=Path, default=Path("data/cache"))
    p.add_argument("--language", default="zh")
    _series(p); _output(p)
    p = sub.add_parser("transcribe", help="将本地音视频发送到 OpenAI 转写，产生 API 费用")
    p.add_argument("file", type=Path)
    p.add_argument("--id")
    p.add_argument("--title")
    p.add_argument("--source-url", default="")
    p.add_argument("--cache", type=Path, default=Path("data/cache"))
    p.add_argument("--language", default="zh")
    _series(p); _output(p)
    p = sub.add_parser("import", help="导入 JSON/SRT/VTT；无人物标签时明确标为待确认")
    p.add_argument("file", type=Path)
    p.add_argument("--id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--source-url", default="")
    p.add_argument("--demo", action="store_true", help="标为自制演示，只允许预览")
    _series(p); _output(p)
    p = sub.add_parser("edit", help="按稳定 ID 应用整理稿、人物、摘要、章节和校对状态")
    p.add_argument("episode", type=Path)
    p.add_argument("--edits", type=Path, required=True)
    p = sub.add_parser("export", help="从单集 JSON 导出文稿")
    p.add_argument("episode", type=Path)
    p.add_argument("--formats", nargs="+", choices=["markdown", "pdf"], default=["markdown", "pdf"])
    p.add_argument("--output-dir", type=Path, default=Path("output/exports"))
    p = sub.add_parser("publish", help="将校对完成的单集标为已发布；不执行公网部署")
    p.add_argument("episode", type=Path)
    p = sub.add_parser("site", help="构建静态阅读站；默认仅含已发布节目")
    p.add_argument("episodes", nargs="+", type=Path, help="单集 JSON 文件或含 episode.json 的目录")
    p.add_argument("--output-dir", type=Path,
                   help="默认正式站 output/site；--preview 时默认 output/preview")
    p.add_argument("--preview", action="store_true", help="包含草稿与演示，仅用于本地预览")
    p = sub.add_parser("validate", help="检查结构、时间戳和人物引用")
    p.add_argument("episode", type=Path)
    return root


def run(args):
    if args.command == "doctor":
        from .doctor import check_environment
        report = check_environment(args.require)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ready"] else 2
    elif args.command == "inspect":
        from .sources import inspect_source
        metadata = inspect_source(args.url)
        if args.output:
            write_json(args.output, metadata)
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    elif args.command == "ingest":
        from .sources import fetch_audio, inspect_source
        from .transcribe import transcribe_audio
        _new_destination(args.output)
        metadata = inspect_source(args.url)
        work = args.cache / safe_id(metadata["id"])
        write_json(work / "metadata.json", metadata)
        audio = fetch_audio(args.url, work)
        segments, speakers = transcribe_audio(audio, args.cache / "asr", language=args.language)
        _save_new(args, metadata, segments, speakers)
    elif args.command in ("transcribe", "import"):
        _new_destination(args.output)
        if args.command == "transcribe":
            from .transcribe import transcribe_audio
            segments, speakers = transcribe_audio(args.file, args.cache / "asr", language=args.language)
        else:
            from .transcripts import read_transcript
            segments, speakers = read_transcript(args.file)
        metadata = {"id": args.id or args.file.stem, "title": args.title or args.file.stem,
                    "source": {"platform": "bilibili" if "bilibili.com" in args.source_url else "local",
                               "url": args.source_url, "author": ""}}
        _save_new(args, metadata, segments, speakers)
    elif args.command == "edit":
        before = load_episode(args.episode, for_edit=True)
        after = apply_edits(before, _read(args.edits))
        backup = args.episode.parent / "history" / f"{before['id']}-r{before.get('revision', 1)}.json"
        if not backup.exists():
            write_json(backup, before)
        save_episode(args.episode, after)
        print(f"已保存草稿 r{after['revision']}：{args.episode.resolve()}")
    elif args.command == "export":
        from .exporters import export_episode
        ep = load_episode(args.episode)
        paths = export_episode(ep, args.output_dir, args.formats)
        ep.setdefault("artifacts", {}).update({key: str(path.resolve()) for key, path in paths.items()})
        save_episode(args.episode, ep)
        print(json.dumps({key: str(path.resolve()) for key, path in paths.items()}, ensure_ascii=False, indent=2))
    elif args.command == "publish":
        ep = load_episode(args.episode)
        validate_episode(ep, for_publication=True)
        ep["status"] = "published"
        ep["updated_at"] = utc_now()
        ep["artifacts"] = {}  # Draft exports must be regenerated with the public status.
        save_episode(args.episode, ep)
        print("已标记为已发布；请重新 export 和 site 构建。尚未部署到公网。")
    elif args.command == "site":
        from .site import build_site
        files = []
        for path in args.episodes:
            files.extend(sorted(path.rglob("episode.json")) if path.is_dir() else [path])
        episodes = []
        for path in dict.fromkeys(files):
            ep = deepcopy(load_episode(path))
            ep["artifacts"] = {key: str((path.parent / value).resolve()) if not Path(value).is_absolute() else value
                               for key, value in ep.get("artifacts", {}).items()}
            episodes.append(ep)
        output_dir = args.output_dir or Path("output/preview" if args.preview else "output/site")
        print(build_site(episodes, output_dir, include_drafts=args.preview).resolve())
    elif args.command == "validate":
        ep = load_episode(args.episode)
        print(f"结构有效：{len(ep['segments'])} 段 / {len(ep['speakers'])} 位说话人 / {ep['status']}")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args) or 0
    except (ContentError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
