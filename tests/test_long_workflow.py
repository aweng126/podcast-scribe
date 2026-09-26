"""CLI integration boundaries for long recordings; no cloud calls."""
import json

from podcast_scribe.cli import main
from podcast_scribe.model import load_episode
from podcast_scribe.transcripts import normalize_segments


def test_transcribe_preserves_four_hour_duration_with_silent_tail(tmp_path, monkeypatch):
    from podcast_scribe import transcribe

    source = tmp_path / "synthetic.mp4"
    source.write_bytes(b"synthetic input; API and decoder are mocked")
    output = tmp_path / "episode.json"

    def fake_transcribe(path, cache_dir, *, language, metadata):
        assert path == source
        metadata.update(duration_seconds=14400, manifest="local-cache-only.json")
        return normalize_segments([
            {"start": 0, "end": 5, "speaker": "A", "text": "合成开场"},
            {"start": 14300, "end": 14310, "speaker": "A", "text": "合成结尾，之后为静音"},
        ])

    monkeypatch.setattr(transcribe, "transcribe_audio", fake_transcribe)
    assert main(["transcribe", str(source), "--id", "long-test", "--title", "合成测试",
                 "--output", str(output)]) == 0
    ep = load_episode(output)
    assert ep["duration_seconds"] == 14400
    assert ep["segments"][-1]["end"] == 14310
    assert all(segment["review_status"] == "unreviewed" for segment in ep["segments"])
    assert "manifest" not in ep
    assert not ep["review"]["content_checked"]


def test_four_hour_transcript_batches_preserve_every_segment(tmp_path, capsys):
    source = tmp_path / "synthetic.json"
    rows = [{"start": i * 10, "end": (i + 1) * 10, "speaker": ("A", "B")[i % 2],
             "text": f"合成段落 {i + 1}：保留事实、数字和否定，不把已整理状态当作完成音频核对。"}
            for i in range(1440)]
    source.write_text(json.dumps({"segments": rows}, ensure_ascii=False), encoding="utf-8")
    episode = tmp_path / "episode.json"
    assert main(["import", str(source), "--id", "long-demo", "--title", "四小时合成数据",
                 "--demo", "--output", str(episode)]) == 0
    capsys.readouterr()
    cursor, visited, number = None, [], 0
    while True:
        number += 1
        batch_path = tmp_path / f"batch-{number}.json"
        args = ["batch", str(episode), "--max-chars", "6000", "--output", str(batch_path)]
        if cursor:
            args += ["--after", cursor]
        assert main(args) == 0
        text = capsys.readouterr().out
        assert len(text) <= 6000
        batch = json.loads(text)
        visited.extend(segment["id"] for segment in batch["targets"])
        edits_path = tmp_path / f"edits-{number}.json"
        edits_path.write_text(json.dumps({"segments": [
            {"id": segment["id"], "review_status": "edited"} for segment in batch["targets"]
        ]}), encoding="utf-8")
        assert main(["edit", str(episode), "--batch", str(batch_path), "--edits", str(edits_path)]) == 0
        capsys.readouterr()
        cursor = batch["next_after"]
        if cursor is None:
            break
    result = load_episode(episode)
    assert result["duration_seconds"] == 14400
    assert visited == [segment["id"] for segment in result["segments"]]
    assert [(s["start"], s["end"], s["raw_text"], s["text"]) for s in result["segments"]] == [
        (row["start"], row["end"], row["text"], row["text"]) for row in rows
    ]
    assert main(["status", str(episode)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["remaining"] == 1440 and status["reviewed"] == 0
    assert main(["export", str(episode), "--formats", "markdown", "--output-dir", str(tmp_path / "exports")]) == 0
    markdown = (tmp_path / "exports" / "long-demo.md").read_text(encoding="utf-8")
    assert all(row["text"] in markdown for row in rows)
