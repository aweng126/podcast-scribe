"""Local production CLI. Publishing marks content; it does not deploy a website."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from .model import (ContentError, REVIEW_BASES, REVIEW_MODES, apply_edits,
                    complete_episode, load_episode, new_episode, save_episode,
                    utc_now, validate_episode, write_json)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _series(parser):
    parser.add_argument("--series-id", help="复用系列目录中的 ID；未知系列需同时提供名称")
    parser.add_argument("--series-title", help="已核实的节目名称；识别目录别名，缺省为未分类")
    parser.add_argument("--series-catalog", type=Path, help="可选的系列目录 JSON；默认使用 Skill 内置目录")


def _output(parser):
    parser.add_argument("--output", type=Path, help="默认 data/<来源 ID>/episode.json；存在时不会覆盖")


def _review_mode(parser):
    parser.add_argument("--review-mode", choices=sorted(REVIEW_MODES), default="auto",
                        help="auto 自动整理完成；precise 保留逐段精校流程（默认 auto）")


def _cookie_options(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--cookies-from-browser", metavar="BROWSER[:PROFILE]",
                       help="明确授权读取所选浏览器的 B站 cookies，仅本次进程内使用")
    group.add_argument("--cookies", type=Path, metavar="FILE",
                       help="明确授权读取 Netscape cookies 文件；不写回或导出 cookies")


def _source_options(args):
    if args.cookies_from_browser is not None or args.cookies is not None:
        from .source_auth import SourceAuth
        return {"auth": SourceAuth(browser=args.cookies_from_browser, cookies=args.cookies)}
    return {}


def _new_destination(path):
    if path.exists() or path.is_symlink():
        raise ContentError(f"文件已存在，已保留人工修改：{path}。请使用 edit/export 或指定新的输出路径。")


def _requested_series(args):
    from .series import load_catalog, resolve_series
    return resolve_series(series_id=args.series_id, series_title=args.series_title,
                          catalog=load_catalog(args.series_catalog))


def _save_new(args, metadata, segments, speakers):
    from .defaults import save_new_episode
    series = args.resolved_series
    ep = new_episode(metadata, segments, speakers, series_id=series["id"],
                     series_title=series["title"], review_mode=args.review_mode)
    ep["series"] = series
    if "input_identity" in metadata:
        ep["input_identity"] = metadata["input_identity"]
    if getattr(args, "demo", False):
        ep["is_demo"] = True
    save_new_episode(args.output, ep)
    print(args.output.resolve())


def _save_revision(path, before, after):
    backup = path.parent / "history" / f"{before['id']}-r{before.get('revision', 1)}.json"
    if not backup.exists():
        write_json(backup, before)
    save_episode(path, after)


def _resume_default(args, identity, *, default_output: bool) -> bool:
    if default_output:
        from .defaults import existing_report
        report = existing_report(args.output, identity, args)
        if report:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return True
    _new_destination(args.output)
    return False


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
    _cookie_options(p)
    p = sub.add_parser("ingest", help="获取 B站音频并调用云端说话人转写")
    p.add_argument("url")
    p.add_argument("--cache", type=Path, default=Path("data/cache"))
    p.add_argument("--language", default="zh")
    _cookie_options(p)
    _series(p); _output(p); _review_mode(p)
    p = sub.add_parser("transcribe", help="将本地音视频发送到 OpenAI 转写，产生 API 费用")
    p.add_argument("file", type=Path)
    p.add_argument("--id")
    p.add_argument("--title")
    p.add_argument("--source-url", default="")
    p.add_argument("--cache", type=Path, default=Path("data/cache"))
    p.add_argument("--language", default="zh")
    _series(p); _output(p); _review_mode(p)
    p = sub.add_parser("import", help="导入 JSON/SRT/VTT；无人物标签时明确标为待确认")
    p.add_argument("file", type=Path)
    p.add_argument("--id")
    p.add_argument("--title")
    p.add_argument("--source-url", default="")
    p.add_argument("--demo", action="store_true", help="标为自制演示，只允许预览")
    _series(p); _output(p); _review_mode(p)
    p = sub.add_parser("edit", help="按稳定 ID 应用整理稿、人物、摘要、章节和校对状态")
    p.add_argument("episode", type=Path)
    p.add_argument("--edits", type=Path, required=True)
    p.add_argument("--batch", type=Path, help="校验 batch 的版本与内容摘要，仅允许修改本批目标段落")
    p = sub.add_parser("complete", help="记录自动整理、用户接受或来源精校完成依据")
    p.add_argument("episode", type=Path)
    p.add_argument("--basis", choices=sorted(REVIEW_BASES),
                   help="省略时 auto 使用 automated，precise 使用 source_checked；user_accepted 仅用于用户明确接受当前稿")
    p = sub.add_parser("status", help="查看精简校对进度，不输出全文")
    p.add_argument("episode", type=Path)
    p = sub.add_parser("series-list", help="读取可复用的节目系列、别名及来源；不联网")
    p.add_argument("--catalog", type=Path, help="已下载的社区系列目录；省略时使用 Skill 内置目录")
    p = sub.add_parser("classify", help="根据已核实来源保存系列归属，保留正文与校对状态")
    p.add_argument("episode", type=Path)
    p.add_argument("--series-id")
    p.add_argument("--series-title")
    p.add_argument("--evidence-url", help="Agent 已核实节目归属的官方来源链接；命令不代替核实")
    p.add_argument("--unclassified", action="store_true", help="无法确认归属时明确保留未分类")
    p.add_argument("--catalog", type=Path)
    p = sub.add_parser("batch", help="按字符预算读取完整段落，默认跳过已校对段落")
    p.add_argument("episode", type=Path)
    p.add_argument("--max-chars", type=int, default=6000, help="完整紧凑 JSON 的字符上限，默认 6000")
    p.add_argument("--after", help="从此稳定段落 ID 之后续读")
    p.add_argument("--include-reviewed", action="store_true", help="同时读取已校对段落")
    p.add_argument("--context-chars", type=int, default=300, help="前后各最多保留的上下文字符数，默认 300")
    p.add_argument("--raw", action="store_true", help="按需读取原始转写，替代当前正文视图")
    p.add_argument("--output", type=Path, help="另存同一紧凑 JSON，供 edit --batch 校验")
    p = sub.add_parser("check-subtitles", help="提取字幕并与现稿对照；不修改正文或校对状态")
    p.add_argument("episode", type=Path)
    source = p.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="本地 JSON/SRT/VTT 字幕；省略时检查 B站字幕轨")
    source.add_argument("--video", type=Path, help="本地视频画面字幕 OCR")
    source.add_argument("--ocr", action="store_true", help="下载所选 B站视频画面并进行本地 OCR")
    p.add_argument("--cache", type=Path, default=Path("data/cache"))
    p.add_argument("--output-dir", type=Path, help="默认 output/<id>/subtitles/；旧报告保留")
    p.add_argument("--refresh", action="store_true", help="重新采集字幕或识别画面，不清除原文稿")
    p.add_argument("--offset", type=float, default=0, help="字幕时间加此秒数后再对齐")
    p.add_argument("--start", type=float, default=0, help="OCR 起点秒数；默认完整视频")
    p.add_argument("--end", type=float, help="OCR 终点秒数；先识别短片检查字幕区域")
    p.add_argument("--interval", type=float, default=0.5, help="OCR 抽帧间隔秒数")
    p.add_argument("--region", type=float, nargs=4, default=(0.05, 0.65, 0.9, 0.3),
                   metavar=("X", "Y", "WIDTH", "HEIGHT"), help="OCR 字幕区域，0–1 归一化坐标")
    p.add_argument("--ocr-language", default="chi_sim+eng")
    _cookie_options(p)
    p = sub.add_parser("subtitle-batch", help="按字符预算读取字幕差异；拒绝与现稿不一致的报告")
    p.add_argument("episode", type=Path)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--after", type=int, default=0, help="从此差异组序号之后继续")
    p.add_argument("--max-chars", type=int, default=6000)
    p.add_argument("--output", type=Path)
    p = sub.add_parser("export", help="从单集 JSON 导出文稿")
    p.add_argument("episode", type=Path)
    p.add_argument("--formats", nargs="+", choices=["markdown", "pdf"], default=["markdown", "pdf"])
    p.add_argument("--output-dir", type=Path, help="默认 output/<单集 ID>/")
    p = sub.add_parser("publish", help="将校对完成的单集标为已发布；不执行公网部署")
    p.add_argument("episode", type=Path)
    p = sub.add_parser("share", help="生成公开投稿 JSON 与 Issue 表单链接；不自动上传或发布")
    p.add_argument("episode", type=Path)
    p.add_argument("--attribution", required=True, help="公开展示的投稿署名")
    p.add_argument("--confirm-public", action="store_true",
                   help="确认可以公开分享；公开仓库 Issue 与附件在收录审核前即已公开")
    p.add_argument("--output", type=Path, help="默认 output/share/<id>.json；存在时不会覆盖")
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
        metadata = inspect_source(args.url, **_source_options(args))
        if args.output:
            write_json(args.output, metadata)
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    elif args.command == "ingest":
        from .defaults import destination_lock, resolved_video_target, video_target
        from .sources import fetch_audio, fetch_subtitles, inspect_source
        from .transcribe import transcribe_audio
        default_output = args.output is None
        if not default_output:
            _new_destination(args.output)
        source_options = _source_options(args)
        target = video_target(args.url)
        if target is None:
            metadata = inspect_source(args.url, **source_options)
            target = resolved_video_target(args.url, metadata)
        args.output = args.output or Path("data") / target["id"] / "episode.json"
        with destination_lock(args.output):
            if _resume_default(args, target["identity"], default_output=default_output):
                return 0
            args.resolved_series = _requested_series(args)
            metadata = inspect_source(target["url"], **source_options)
            resolved_video_target(target["url"], metadata)
            metadata["id"] = target["id"]
            metadata["source"].update(url=target["url"], video_id=target["identity"]["video_id"])
            metadata["input_identity"] = target["identity"]
            work = args.cache / target["id"]
            write_json(work / "metadata.json", metadata)
            # Subtitle evidence is optional, and remains separate from the ASR
            # source and review states. Query once before the paid audio step.
            try:
                subtitle_document = fetch_subtitles(target["url"], work, **source_options)
            except ContentError as exc:
                subtitle_document = {"schema_version": 1, "status": "unavailable", "cues": [],
                                     "source": {"kind": "bilibili", "url": target["url"]},
                                     "reason": str(exc)}
            audio = fetch_audio(target["url"], work, **source_options)
            audio_metadata = {}
            segments, speakers = transcribe_audio(audio, args.cache / "asr", language=args.language,
                                                 metadata=audio_metadata)
            metadata["duration_seconds"] = max(metadata.get("duration_seconds") or 0,
                                               audio_metadata.get("duration_seconds") or 0)
            _save_new(args, metadata, segments, speakers)
            from .subtitle_review import write_subtitle_report
            try:
                result = write_subtitle_report(load_episode(args.output), subtitle_document)
                print(json.dumps({"subtitle_check": result}, ensure_ascii=False), file=sys.stderr)
            except ContentError as exc:
                print(f"字幕对照未完成，已保留转写稿：{exc}", file=sys.stderr)
    elif args.command in ("transcribe", "import"):
        from .defaults import destination_lock, local_target
        default_output = args.output is None
        if not default_output:
            _new_destination(args.output)
        target = local_target(args.file, args.command, ident=args.id, source_url=args.source_url)
        args.output = args.output or Path("data") / target["id"] / "episode.json"
        with destination_lock(args.output):
            if _resume_default(args, target["identity"], default_output=default_output):
                return 0
            args.resolved_series = _requested_series(args)
            audio_metadata = {}
            if args.command == "transcribe":
                from .transcribe import transcribe_audio
                segments, speakers = transcribe_audio(args.file, args.cache / "asr", language=args.language,
                                                     metadata=audio_metadata)
            else:
                from .transcripts import read_transcript
                segments, speakers = read_transcript(args.file)
            metadata = {"id": target["id"], "title": args.title or target["title"],
                        "duration_seconds": audio_metadata.get("duration_seconds", 0),
                        "input_identity": target["identity"],
                        "source": {"platform": "bilibili" if "bilibili.com" in target["source_url"] else "local",
                                   "url": target["source_url"], "author": ""}}
            _save_new(args, metadata, segments, speakers)
    elif args.command == "status":
        from .editing import compact_json, editing_status
        print(compact_json(editing_status(load_episode(args.episode))), end="")
    elif args.command == "series-list":
        from .series import load_catalog
        print(json.dumps(load_catalog(args.catalog), ensure_ascii=False, indent=2))
    elif args.command == "classify":
        from .defaults import destination_lock
        from .series import UNCATEGORIZED, load_catalog, resolve_series, validate_evidence_url
        if args.unclassified:
            if args.series_id is not None or args.series_title is not None or args.evidence_url is not None:
                raise ContentError("--unclassified 不能同时指定系列或来源")
            series = deepcopy(UNCATEGORIZED)
        else:
            if not args.series_id and not args.series_title:
                raise ContentError("请提供已核实的 --series-id/--series-title，或使用 --unclassified")
            if not args.evidence_url:
                raise ContentError("分类需要 --evidence-url 记录已核实的节目归属来源")
            validate_evidence_url(args.evidence_url)
            series = resolve_series(series_id=args.series_id, series_title=args.series_title,
                                    catalog=load_catalog(args.catalog))
        with destination_lock(args.episode):
            before = load_episode(args.episode)
            after = deepcopy(before)
            after["series"] = series
            # A dedicated, recognizable reference keeps classification evidence
            # separate from source/person references, and is safe to replace.
            prefix = "系列归属："
            after["references"] = [ref for ref in before.get("references", [])
                                   if not ref.get("title", "").startswith(prefix)]
            if not args.unclassified:
                after["references"].append({"title": prefix + series["title"], "url": args.evidence_url})
            changed = (after["series"] != before["series"]
                       or after["references"] != before.get("references", []))
            if changed:
                after["revision"] = before.get("revision", 1) + 1
                after["updated_at"] = utc_now()
                after["artifacts"] = {}
                validate_episode(after)
                _save_revision(args.episode, before, after)
            print(json.dumps({"episode": before["id"], "series": series, "changed": changed,
                              "revision": after.get("revision", 1),
                              "message": "系列归属已保存；正文和完成方式保持不变，已生成的导出或投稿文件需重新生成。"
                              if changed else "系列归属未变化，保留已有文件。"}, ensure_ascii=False, indent=2))
    elif args.command == "batch":
        from .editing import compact_json, read_batch
        batch = read_batch(load_episode(args.episode), max_chars=args.max_chars, after=args.after,
                           include_reviewed=args.include_reviewed, context_chars=args.context_chars, raw=args.raw)
        content = compact_json(batch)
        if args.output:
            _new_destination(args.output)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(content)
        print(content, end="")
    elif args.command == "check-subtitles":
        from .subtitle_review import check_episode_subtitles
        result = check_episode_subtitles(args.episode, subtitle_file=args.file, video=args.video,
                    ocr=args.ocr, cache=args.cache, output_dir=args.output_dir,
                    refresh=args.refresh, offset=args.offset, start=args.start, end=args.end,
                    interval=args.interval, region=args.region, language=args.ocr_language,
                    **_source_options(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"available", "no_subtitles"} else 2
    elif args.command == "subtitle-batch":
        from .editing import compact_json
        from .subtitle_review import read_difference_batch
        result = read_difference_batch(load_episode(args.episode), _read(args.report),
                                       after=args.after, max_chars=args.max_chars)
        content = compact_json(result)
        if args.output:
            _new_destination(args.output)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(content)
        print(content, end="")
    elif args.command == "edit":
        before = load_episode(args.episode, for_edit=True)
        edits = _read(args.edits)
        if args.batch:
            from .editing import validate_batch_edits
            validate_batch_edits(before, edits, _read(args.batch))
        after = apply_edits(before, edits)
        _save_revision(args.episode, before, after)
        print(f"已保存草稿 r{after['revision']}：{args.episode.resolve()}")
    elif args.command == "complete":
        before = load_episode(args.episode, for_edit=True)
        after = complete_episode(before, basis=args.basis)
        _save_revision(args.episode, before, after)
        print(f"已完成（{after['review']['basis']}）并保存草稿 r{after['revision']}：{args.episode.resolve()}")
    elif args.command == "export":
        from .exporters import export_episode
        ep = load_episode(args.episode)
        paths = export_episode(ep, args.output_dir or Path("output") / ep["id"], args.formats)
        ep.setdefault("artifacts", {}).update({key: str(path.resolve()) for key, path in paths.items()})
        save_episode(args.episode, ep)
        print(json.dumps({key: str(path.resolve()) for key, path in paths.items()}, ensure_ascii=False, indent=2))
    elif args.command == "share":
        from .share import canonical_bytes, issue_url, make_submission, submission_digest
        if not args.confirm_public:
            raise ContentError("分享前请确认可以公开正文、来源和署名，并提供 --confirm-public；Issue 与附件在审核前即已公开。")
        submission = make_submission(load_episode(args.episode), attribution=args.attribution)
        destination = args.output or Path("output/share") / f"{submission['episode']['id']}.json"
        _new_destination(destination)
        url = issue_url(submission)
        payload = canonical_bytes(submission)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(payload)
        upload_hint = ("文件超过 Issue 附件限制，请先上传到公开 GitHub Release，再将 .json 资产直链填入投稿表单。"
                       if len(payload) > 25_000_000 else "请在投稿表单中上传该 JSON 文件。")
        print(json.dumps({"file": str(destination.resolve()), "sha256": submission_digest(submission),
                          "size_bytes": len(payload),
                          "issue_url": url,
                          "message": "请先检查公开 JSON。" + upload_hint + "当前仅生成投稿材料，尚未提交；收录审核通过并部署后才会出现在公共阅读站。"},
                         ensure_ascii=False, indent=2))
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
    from .defaults import SourceConflict
    args = parser().parse_args(argv)
    try:
        return run(args) or 0
    except SourceConflict as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        return 2
    except (ContentError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
