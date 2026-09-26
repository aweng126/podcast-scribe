"""Run the public script entrypoints from a separate user's task workspace."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1] / "skills/podcast-scribe"


def run_script(workspace, name, *args):
    env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # -I and -S isolate this standard-library-only path from the developer's
    # editable installs, third-party dependencies and PYTHONPATH.
    return subprocess.run(
        [sys.executable, "-I", "-S", str(ROOT / "scripts" / name), *args],
        cwd=workspace, env=env, capture_output=True, text=True, check=True, timeout=30,
    )


def test_complete_offline_workflow_in_separate_workspace(tmp_path):
    rows = [
        {"start": 0, "end": 4, "speaker": "A", "text": "嗯，我们只观察了十二个样本。"},
        {"start": 4, "end": 8, "speaker": "B", "text": "不能据此证明对所有人有效。"},
    ]
    (tmp_path / "input.json").write_text(json.dumps({"segments": rows}), encoding="utf-8")
    cli = lambda *args: run_script(tmp_path, "podcast_scribe.py", *args)
    report = json.loads(cli("doctor", "--require", "import", "markdown", "site").stdout)
    assert report["ready"] is True
    cli("import", "input.json", "--id", "workspace-demo", "--title", "离线任务演示",
        "--demo", "--output", "data/demo/episode.json")
    edits = {
        "segments": [{"id": "seg-00001", "text": "我们只观察了十二个样本。", "review_status": "edited"}],
        "summary": ["十二个样本不足以证明普遍有效。"],
        "chapters": [{"id": "c1", "title": "样本与结论", "start": 0, "segment_id": "seg-00001"}],
    }
    (tmp_path / "edits.json").write_text(json.dumps(edits), encoding="utf-8")
    cli("edit", "data/demo/episode.json", "--edits", "edits.json")
    cli("validate", "data/demo/episode.json")
    cli("export", "data/demo/episode.json", "--formats", "markdown")
    cli("site", "data", "--preview")

    episode = json.loads((tmp_path / "data/demo/episode.json").read_text(encoding="utf-8"))
    assert episode["is_demo"] and episode["status"] == "draft"
    assert [s["raw_text"] for s in episode["segments"]] == [s["text"] for s in rows]
    assert [(s["start"], s["end"]) for s in episode["segments"]] == [(0, 4), (4, 8)]
    assert (tmp_path / "data/demo/history/workspace-demo-r1.json").is_file()
    md = (tmp_path / "output/workspace-demo/workspace-demo.md").read_text(encoding="utf-8")
    assert "我们只观察了十二个样本。" in md and "不能据此证明对所有人有效。" in md
    assert "说话人 1" in md and "说话人 2" in md
    page = (tmp_path / "output/preview/index.html").read_text(encoding="utf-8")
    match = re.search(r'<script id="transcript-data" type="application/json">(.*?)</script>', page, re.S)
    public = json.loads(match.group(1))
    download = public["episodes"][0]["downloads"]["markdown"]
    assert (tmp_path / "output/preview" / download).read_text(encoding="utf-8") == md
    assert (tmp_path / "output/preview/app.js").is_file()
    assert not (tmp_path / "output/site").exists()
    cli("site", "data")
    formal_page = (tmp_path / "output/site/index.html").read_text(encoding="utf-8")
    formal_data = json.loads(re.search(r'<script id="transcript-data" type="application/json">(.*?)</script>', formal_page, re.S).group(1))
    assert formal_data["preview"] is False and formal_data["episodes"] == []
    cli("site", "data", "--preview", "--output-dir", "custom-preview")
    assert (tmp_path / "custom-preview/index.html").is_file()


def test_demo_defaults_to_current_workspace_without_pdf_dependencies(tmp_path):
    run_script(tmp_path, "demo.py", "--formats", "markdown")
    episode = json.loads((tmp_path / "output/demo/episode.json").read_text(encoding="utf-8"))
    assert episode["is_demo"] and episode["status"] == "draft"
    assert Path(episode["artifacts"]["markdown"]).parent == tmp_path / "output/demo"
    assert (tmp_path / "output/demo/preview/index.html").is_file()
    assert not list(tmp_path.rglob("*.pdf"))


def test_demo_respects_explicit_output_directory(tmp_path):
    run_script(tmp_path, "demo.py", "--formats", "markdown", "--output-dir", "custom/demo")
    assert (tmp_path / "custom/demo/preview/index.html").is_file()
    assert not (tmp_path / "output").exists()
