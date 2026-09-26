#!/usr/bin/env python3
"""Freeze a public Issue attachment into one reviewable, content-only PR.

Only GitHub's event file and API responses supply repository/Issue provenance.
The attachment is data: it is never evaluated, extracted or used as a path.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "podcast-scribe"))

from podcast_scribe.model import ContentError
from podcast_scribe.share import MAX_SUBMISSION_BYTES, loads_submission, submission_digest
from podcast_scribe.public_site import loads_record, validate_record

REPOSITORY = "aweng126/podcast-scribe"
CONSENT = "我已检查投稿文件，确认可以公开分享，并同意在本项目及公共阅读站展示。"
FIELDS = ("来源链接", "投稿署名", "内容 SHA-256", "投稿 JSON", "公开分享确认")
COMMENT_MARKER = "<!-- podcast-scribe-submission -->"
PR_MARKER = "<!-- podcast-scribe-frozen-submission -->"


class IntakeError(ValueError):
    pass


def parse_form(body: str) -> dict[str, str]:
    if not isinstance(body, str) or len(body.encode("utf-8")) > 65536:
        raise IntakeError("Issue 正文无效或超过 64 KiB")
    matches = list(re.finditer(r"^### ([^\r\n]+)\r?$", body, re.MULTILINE))
    fields = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        if name in fields:
            raise IntakeError("Issue 表单包含重复字段")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        fields[name] = body[match.end():end].strip()
    if set(fields) != set(FIELDS) or any(not fields[name] for name in FIELDS):
        raise IntakeError("请使用分享文稿表单，完整填写所有字段")
    if fields["公开分享确认"] not in (f"- [X] {CONSENT}", f"- [x] {CONSENT}"):
        raise IntakeError("需要确认投稿内容可以公开分享")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", fields["内容 SHA-256"]):
        raise IntakeError("内容 SHA-256 应为 share 命令输出的 64 位摘要")
    return fields


def attachment_url(value: str) -> str:
    # Accept exactly one attachment link, including GitHub's normal Markdown wrapper.
    match = re.fullmatch(r"(?:\[[^\]\r\n]*\]\((https://[^\s<>]+)\)|(https://[^\s<>]+))", value.strip())
    if not match:
        raise IntakeError("请上传一个 JSON 附件，不要粘贴正文或外部下载链接")
    url = match.group(1) or match.group(2)
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment
            or not re.fullmatch(r"/user-attachments/files/[1-9][0-9]*/[A-Za-z0-9_.-]+\.json", parts.path)):
        raise IntakeError("只接受 github.com/user-attachments/files/ 下的 JSON 附件")
    return url


def allowed_download(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        return False
    if parts.hostname == "github.com":
        return parts.path.startswith("/user-attachments/files/")
    if parts.hostname == "objects.githubusercontent.com":
        return parts.path.startswith(("/github-production-repository-file-", "/github-production-user-asset-"))
    return parts.hostname in {
        "github-production-user-asset-6210df.s3.amazonaws.com",
        "github-production-repository-file-5c1aeb.s3.amazonaws.com",
    }


class AttachmentRedirects(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_download(newurl):
            raise IntakeError("GitHub 附件重定向到了不支持的地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise IntakeError("GitHub API 不应重定向；请检查仓库名称")


def download_submission(url: str) -> dict:
    url = attachment_url(url)
    request = Request(url, headers={"Accept": "application/json, application/octet-stream", "User-Agent": "podcast-scribe-intake"})
    # This client deliberately has no Authorization header, even for GitHub redirects.
    with build_opener(AttachmentRedirects()).open(request, timeout=20) as response:
        length = response.headers.get("Content-Length")
        if length and (not length.isdigit() or int(length) > MAX_SUBMISSION_BYTES):
            raise IntakeError("投稿文件超过 2 MiB 或长度无效")
        payload = response.read(MAX_SUBMISSION_BYTES + 1)
    if len(payload) > MAX_SUBMISSION_BYTES:
        raise IntakeError("投稿文件超过 2 MiB")
    return loads_submission(payload)


def event_identity(event: dict, repository: str) -> tuple[int, str, str]:
    if repository != REPOSITORY or event.get("repository", {}).get("full_name") != repository:
        raise IntakeError("事件仓库与预期仓库不一致")
    if event.get("action") not in {"opened", "edited", "reopened"}:
        raise IntakeError("不支持的 Issue 事件")
    issue = event.get("issue", {})
    number = issue.get("number")
    if type(number) is not int or number <= 0 or issue.get("pull_request"):
        raise IntakeError("需要普通 Issue 事件")
    url = f"https://github.com/{repository}/issues/{number}"
    login = issue.get("user", {}).get("login", "")
    if issue.get("html_url") != url or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?", login):
        raise IntakeError("Issue 地址或投稿者无效")
    if not issue.get("title", "").startswith("[分享]") or issue.get("state") != "open":
        raise IntakeError("仅处理处于打开状态的分享投稿")
    return number, url, login


def make_record(event: dict, repository: str, download=download_submission) -> dict:
    _, url, login = event_identity(event, repository)
    fields = parse_form(event["issue"].get("body"))
    submission = download(attachment_url(fields["投稿 JSON"]))
    digest = submission_digest(submission)
    if digest != fields["内容 SHA-256"].lower():
        raise IntakeError("内容 SHA-256 与附件不一致，请重新生成分享文件及链接")
    if fields["来源链接"] != submission["episode"]["source"]["url"]:
        raise IntakeError("表单来源链接与附件不一致")
    if fields["投稿署名"] != submission["attribution"]:
        raise IntakeError("表单投稿署名与附件不一致")
    return validate_record({"schema_version": 1, "submission": submission, "provenance": {
        "issue_url": url, "submitter": login, "payload_sha256": digest,
    }})


class GitHub:
    def __init__(self, token: str, repository: str):
        if not token or repository != REPOSITORY:
            raise IntakeError("需要本仓库的 GH_TOKEN")
        self.token = token
        self.repository = repository
        self.opener = build_opener(NoRedirects())

    def call(self, method: str, path: str, data=None, *, missing_ok=False):
        if not path.startswith("/") or path.startswith("//"):
            raise IntakeError("无效 GitHub API 路径")
        request = Request("https://api.github.com/repos/" + self.repository + path,
                          data=None if data is None else json.dumps(data).encode(), method=method,
                          headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28",
                                   "User-Agent": "podcast-scribe-intake"})
        try:
            with self.opener.open(request, timeout=20) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise IntakeError("GitHub API 响应过大")
                return json.loads(body) if body else None
        except HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            if error.code == 403 and method == "POST" and path == "/pulls":
                raise IntakeError("创建 PR 被拒绝。请在 Settings → Actions → General → Workflow permissions 启用 Allow GitHub Actions to create and approve pull requests，然后重跑本工作流。") from None
            raise IntakeError(f"GitHub API {method} 请求失败（HTTP {error.code}）；请检查 Actions 权限并重跑工作流") from None


def record_bytes(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def pull_body(record: dict) -> str:
    provenance = record["provenance"]
    return (f"{PR_MARKER}\n\n收录来自 {provenance['issue_url']} 的固定投稿。\n\n"
            f"内容 SHA-256：`{provenance['payload_sha256']}`\n\n"
            "请核对来源、正文、署名与公开分享确认。此 PR 只新增一份公开 JSON；Issue 后续编辑不会改变此快照。"
            "如机器人创建的 PR 检查显示等待批准，请先批准运行检查，再人工合并。合并后 Pages 自动更新。\n\n"
            f"合并且 Pages 部署成功后可阅读：{reader_url(record)}\n\n"
            f"Closes {provenance['issue_url']}\n")


def reader_url(record: dict) -> str:
    return "https://aweng126.github.io/podcast-scribe/#/episode/" + quote(record["submission"]["episode"]["id"], safe="")


def freeze_submission(api, event: dict, repository: str, download=download_submission) -> str:
    number, url, login = event_identity(event, repository)
    branch = f"community/issue-{number}"
    path = f"content/episodes/issue-{number}.json"
    if api.call("GET", f"/contents/{path}?ref=main", missing_ok=True):
        return "该 Issue 已收录。修改文稿请新建分享 Issue，注明原投稿链接。"
    pulls = api.call("GET", "/pulls?" + urlencode({"state": "all", "head": repository.split("/")[0] + ":" + branch, "base": "main", "per_page": 100}))
    if len(pulls) > 1:
        raise IntakeError("发现多个投稿 PR，请维护者检查该 Issue 的投稿分支")
    if pulls:
        pull = pulls[0]
        if not pull.get("body", "").startswith(PR_MARKER):
            raise IntakeError("投稿分支已有非自动生成的 PR，请维护者检查")
        if pull.get("state") == "open":
            return f"投稿已固定，等待维护者审核：{pull['html_url']} 。Issue 编辑不会更新该快照；修改正文请新建分享 Issue。"
        return "该 Issue 的投稿 PR 已关闭或合并。再次投稿请新建分享 Issue。"

    ref = api.call("GET", f"/git/ref/heads/{branch}", missing_ok=True)
    if ref:
        # Retry after branch/commit creation succeeded but PR creation failed. Never modify the branch.
        compare = api.call("GET", f"/compare/main...{branch}")
        files = compare.get("files", [])
        if len(files) != 1 or files[0].get("filename") != path or files[0].get("status") != "added":
            raise IntakeError("已有投稿分支并非单文件新增，拒绝覆盖；请维护者检查")
        stored = api.call("GET", f"/contents/{path}?ref=" + quote(branch, safe=""))
        if stored.get("type") != "file" or stored.get("size", 0) > MAX_SUBMISSION_BYTES + 4096:
            raise IntakeError("已有投稿文件格式无效")
        if stored.get("encoding") == "none" and re.fullmatch(r"[a-f0-9]{40}", stored.get("sha", "")):
            # Contents responses omit inline data for files larger than 1 MiB.
            stored = api.call("GET", "/git/blobs/" + stored["sha"])
        if stored.get("encoding") != "base64" or stored.get("size", 0) > MAX_SUBMISSION_BYTES + 4096:
            raise IntakeError("已有投稿文件编码或大小无效")
        payload = base64.b64decode(stored["content"], validate=False)
        if len(payload) > MAX_SUBMISSION_BYTES + 4096:
            raise IntakeError("已有投稿文件过大")
        record = loads_record(payload)
        if record["provenance"]["issue_url"] != url or record["provenance"]["submitter"] != login:
            raise IntakeError("已有投稿快照来源不匹配，拒绝覆盖")
    else:
        record = make_record(event, repository, download)
        main = api.call("GET", "/git/ref/heads/main")["object"]["sha"]
        # Pin the base and recheck: main may have advanced while the attachment downloaded.
        if api.call("GET", f"/contents/{path}?ref=" + main, missing_ok=True):
            return "该 Issue 已收录。修改文稿请新建分享 Issue，注明原投稿链接。"
        commit = api.call("GET", "/git/commits/" + main)
        blob = api.call("POST", "/git/blobs", {"encoding": "base64", "content": base64.b64encode(record_bytes(record)).decode()})
        tree = api.call("POST", "/git/trees", {"base_tree": commit["tree"]["sha"], "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
        new_commit = api.call("POST", "/git/commits", {"message": f"Add community submission from issue #{number}", "tree": tree["sha"], "parents": [main]})
        # Creating the ref last makes the operation resumable without a half-written branch.
        api.call("POST", "/git/refs", {"ref": "refs/heads/" + branch, "sha": new_commit["sha"]})
    pull = api.call("POST", "/pulls", {"title": f"收录分享文稿 #{number}", "head": branch, "base": "main", "body": pull_body(record)})
    return f"投稿已固定，等待维护者审核：{pull['html_url']} 。合并且 Pages 部署成功后可阅读：{reader_url(record)}"


def report(api, number: int, message: str) -> None:
    # Upsert our own feedback only. Never edit a user's comments or include attachment text.
    comments = api.call("GET", f"/issues/{number}/comments?per_page=100")
    body = f"{COMMENT_MARKER}\n\n{message}"
    for comment in comments:
        if comment.get("user", {}).get("login") == "github-actions[bot]" and comment.get("body", "").startswith(COMMENT_MARKER):
            if comment["body"] != body:
                api.call("PATCH", f"/issues/comments/{int(comment['id'])}", {"body": body})
            return
    api.call("POST", f"/issues/{number}/comments", {"body": body})


def main() -> int:
    api = None
    number = None
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        number, _, _ = event_identity(event, repository)
        api = GitHub(os.environ.get("GH_TOKEN", ""), repository)
        message = freeze_submission(api, event, repository)
        report(api, number, message)
        print(message)
        return 0
    except (ContentError, IntakeError, OSError, URLError, KeyError, ValueError) as error:
        message = "投稿未完成：" + " ".join(str(error).split())[:800]
        if api is not None and number is not None:
            try:
                report(api, number, message)
            except (IntakeError, OSError, ValueError):
                pass
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
