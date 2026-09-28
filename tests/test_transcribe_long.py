"""Long-recording contract tests; all API calls are local mocks."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
from types import SimpleNamespace
import wave

import pytest

from podcast_scribe import audio_chunks
from podcast_scribe import transcribe as module
from podcast_scribe.model import ContentError, write_json


def speech(speaker="A", start=0, end=4, text="合成接口测试文字"):
    return {"start": start, "end": end, "speaker": speaker, "text": text}


@pytest.fixture
def mocked_pipeline(monkeypatch, tmp_path):
    import openai
    source = tmp_path / "recording.mp3"
    source.write_bytes(b"synthetic test source, never uploaded")
    state = {"duration": 1805, "silences": [], "results": [], "calls": [], "extracts": [], "sleeps": []}

    def prepare(source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return destination

    def extract(source, destination, start, end, *, reference=False):
        state["extracts"].append((start, end, reference))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"{start}:{end}:{reference}".encode())
        return destination

    def create(**kwargs):
        state["calls"].append({**kwargs, "file": Path(kwargs["file"].name).name})
        result = state["results"].pop(0)
        if isinstance(result, Exception):
            raise result
        assert kwargs["stream"] is True
        events = [{"type": "transcript.text.segment", "id": f"part-{i}", **row}
                  for i, row in enumerate(result.get("segments", []))]
        done = {"type": "transcript.text.done", "text": result.get("text", "".join(row["text"] for row in result.get("segments", [])))}
        if "duration" in result:
            done["duration"] = result["duration"]
        class Stream:
            def __iter__(self): return iter([*events, done])
            def close(self): pass
        return Stream()

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"max_retries": 0, "timeout": 600}
            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=create))
        def __enter__(self): return self
        def __exit__(self, *args): pass

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(module, "prepare_audio", prepare)
    monkeypatch.setattr(module, "analyze_audio", lambda _: {"duration": state["duration"], "silences": state["silences"]})
    monkeypatch.setattr(module, "extract_audio", extract)
    monkeypatch.setattr(module.time, "sleep", state["sleeps"].append)
    state.update(source=source, cache=tmp_path / "cache")
    return state


def run(state, **kwargs):
    return module.transcribe_audio(state["source"], state["cache"], **kwargs)


def manifest(state):
    path = next(state["cache"].glob("*/manifest.json"))
    return path, json.loads(path.read_text())


def test_four_hour_plan_has_no_gaps_duplicates_or_oversize_chunks():
    duration = 4 * 60 * 60
    chunks = audio_chunks.plan_chunks(duration, [[899, 899.8], [1790, 1792]])
    assert chunks[0]["end"] == 899.4
    assert chunks[0]["boundary"] == "silence"
    assert chunks[0]["start"] == 0 and chunks[-1]["end"] == duration
    assert all(left["end"] == right["start"] for left, right in zip(chunks, chunks[1:]))
    assert sum(c["end"] - c["start"] for c in chunks) == duration
    assert all(c["end"] - c["start"] <= 900 for c in chunks)
    assert all((c["end"] - c["start"]) * 4000 + 65536 <= module.MAX_UPLOAD_BYTES for c in chunks)


def test_planner_obeys_file_size_as_well_as_duration(monkeypatch):
    monkeypatch.setattr(audio_chunks, "MAX_UPLOAD_BYTES", 4_065_536)
    chunks = audio_chunks.plan_chunks(2000, [], max_seconds=2000)
    assert max(c["end"] - c["start"] for c in chunks) == 900
    monkeypatch.setattr(audio_chunks, "MAX_UPLOAD_BYTES", 2_065_536)
    chunks = audio_chunks.plan_chunks(2000, [])
    assert max(c["end"] - c["start"] for c in chunks) == 500


def test_planner_recognizes_silence_spanning_the_search_window():
    chunks = audio_chunks.plan_chunks(1800, [[100, 1100]])
    assert chunks[0]["boundary"] == "silence"
    assert 870 <= chunks[0]["end"] <= 900


def test_four_hour_orchestration_keeps_all_timestamps(mocked_pipeline):
    state = mocked_pipeline
    state["duration"] = 14400
    state["results"] = [{"duration": 900, "segments": [speech(start=0, end=900, text=f"第 {i} 片")]} for i in range(16)]
    metadata = {}
    segments, people = run(state, metadata=metadata)
    assert len(state["calls"]) == len(segments) == 16
    assert segments[0]["start"] == 0 and segments[-1]["end"] == 14400
    assert [s["text"] for s in segments] == [f"第 {i} 片" for i in range(16)]
    # Independent A labels never merge, even when a provider ignores references.
    assert len(people) == 16
    assert metadata["duration_seconds"] == 14400
    assert metadata["forced_boundary_seconds"] == list(range(900, 14400, 900))
    assert all(s["review_status"] == "needs_review" for s in segments)
    assert len(state["calls"][-1]["extra_body"]["known_speaker_names"]) == 4


def test_failure_resumes_only_pending_chunks_and_keeps_global_speakers(mocked_pipeline, monkeypatch):
    state = mocked_pipeline
    failure = RuntimeError("unauthorized")
    failure.status_code = 401
    state["results"] = [{"segments": [speech()]}, failure]
    with pytest.raises(ContentError, match="HTTP 401"):
        run(state)
    path, before = manifest(state)
    first_response = path.parent / "chunks/chunk-0001.json"
    first_bytes = first_response.read_bytes()
    assert [c["status"] for c in before["chunks"]] == ["complete", "failed", "pending"]
    assert not (path.parent / "transcription.json").exists()
    state["results"] = [{"segments": [speech("voice-0001")]}, {"segments": [speech("A", end=1)]}]
    metadata = {}
    segments, people = run(state, metadata=metadata)
    assert [c["file"] for c in state["calls"]] == ["chunk-0001.mp3", "chunk-0002.mp3", "chunk-0002.mp3", "chunk-0003.mp3"]
    assert first_response.read_bytes() == first_bytes
    assert [s["start"] for s in segments] == [0, 900, 1800]
    assert segments[0]["speaker_id"] == segments[1]["speaker_id"] != segments[2]["speaker_id"]
    assert len(people) == 2
    assert metadata["duration_seconds"] == 1805  # retains trailing silence
    references = state["calls"][2]["extra_body"]
    assert references["known_speaker_names"] == ["voice-0001"]
    assert references["known_speaker_references"][0].startswith("data:audio/wav;base64,")
    monkeypatch.delenv("OPENAI_API_KEY")
    cached_metadata = {}
    assert run(state, metadata=cached_metadata) == (segments, people)
    assert len(state["calls"]) == 4 and cached_metadata == metadata


def test_empty_chunk_is_successful_and_reused(mocked_pipeline):
    state = mocked_pipeline
    state["results"] = [{"segments": []}, {"segments": [speech(end=1)]}, {"segments": []}]
    segments, _ = run(state)
    assert len(segments) == 1 and segments[0]["start"] == 900
    _, saved = manifest(state)
    assert [c["segment_count"] for c in saved["chunks"]] == [0, 1, 0]
    assert all(c["status"] == "complete" for c in saved["chunks"])
    run(state)
    assert len(state["calls"]) == 3


def test_all_silence_is_cached_without_repeated_api_calls(mocked_pipeline, monkeypatch):
    state = mocked_pipeline
    state["results"] = [{"segments": []}] * 3
    with pytest.raises(ContentError, match="没有可用转写内容"):
        run(state)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ContentError, match="没有可用转写内容"):
        run(state)
    assert len(state["calls"]) == 3


def test_silent_subtitle_gap_can_be_cached_and_reused_without_a_key(mocked_pipeline, monkeypatch):
    state = mocked_pipeline
    state["duration"] = 20
    state["results"] = [{"segments": []}]
    metadata = {}
    assert run(state, metadata=metadata, allow_empty=True) == ([], [])
    assert metadata["duration_seconds"] == 20
    monkeypatch.delenv("OPENAI_API_KEY")
    assert run(state, allow_empty=True) == ([], [])
    # The opt-in must not change the normal empty-recording error or rebill it.
    with pytest.raises(ContentError, match="没有可用转写内容"):
        run(state)
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("payload", [
    {"segments": [speech(start=float("nan"))]},
    {"segments": [speech(end=float("inf"))]},
    {"segments": [speech(start=-1)]},
    {"segments": [speech(start=5, end=4)]},
    {"segments": [speech(end=902)]},
    {"segments": [speech(start=10, end=12), speech(start=1, end=2)]},
    {"segments": [speech(speaker=3)]},
    {"segments": [], "text": "丢失时间戳的正文"},
    {"segments": [], "duration": 200},
])
def test_invalid_response_never_becomes_success_cache(mocked_pipeline, payload):
    state = mocked_pipeline
    state["results"] = [payload]
    with pytest.raises(ContentError):
        run(state)
    path, saved = manifest(state)
    assert saved["chunks"][0]["status"] == "failed"
    assert not (path.parent / "chunks/chunk-0001.json").exists()
    assert not (path.parent / "transcription.json").exists()


def test_voice_reference_never_includes_another_overlapping_turn(mocked_pipeline):
    state = mocked_pipeline
    state["duration"] = 1800
    state["results"] = [
        {"segments": [speech("A", 0, 5), speech("B", 1, 4), speech("C", 7, 8)]},
        {"segments": [speech("A", 0, 1)]},
    ]
    segments, people = run(state)
    assert not any(reference for _, _, reference in state["extracts"])
    assert "extra_body" not in state["calls"][1]
    assert len(people) == 4
    assert segments[0]["speaker_id"] != segments[-1]["speaker_id"]


def test_provider_cannot_claim_a_reference_name_that_was_not_sent(mocked_pipeline):
    state = mocked_pipeline
    state["duration"] = 1800
    state["results"] = [{"segments": [speech("voice-0001", end=1)]}, {"segments": [speech("voice-0001", end=1)]}]
    segments, people = run(state)
    assert len(people) == 2
    assert segments[0]["speaker_id"] != segments[1]["speaker_id"]


def test_transient_retry_is_bounded_and_auth_errors_not_retried(mocked_pipeline):
    state = mocked_pipeline
    failure = RuntimeError("provider unavailable")
    failure.status_code = 503
    state["results"] = [failure] * 3
    with pytest.raises(ContentError, match="HTTP 503"):
        run(state)
    assert len(state["calls"]) == 3
    assert state["sleeps"] == [1, 2]


def test_legacy_success_cache_needs_no_key_or_conversion(mocked_pipeline, monkeypatch):
    state = mocked_pipeline
    digest = hashlib.sha256(state["source"].read_bytes())
    digest.update(f"{module.MODEL}:zh:mono16k32kbps:v1".encode())
    write_json(state["cache"] / digest.hexdigest() / "transcription.json", {"segments": [speech()]})
    monkeypatch.delenv("OPENAI_API_KEY")
    metadata = {}
    segments, _ = run(state, metadata=metadata)
    assert segments[0]["text"] == "合成接口测试文字"
    assert not state["calls"] and metadata["legacy_cache"] is True


def test_source_and_language_changes_do_not_reuse_wrong_cache(mocked_pipeline):
    state = mocked_pipeline
    state["duration"] = 5
    state["results"] = [{"segments": [speech(text="原录音")]}, {"segments": [speech(text="新录音")]}, {"segments": [speech(text="new language")]}]
    assert run(state)[0][0]["text"] == "原录音"
    state["source"].write_bytes(b"changed source")
    assert run(state)[0][0]["text"] == "新录音"
    assert run(state, language="en")[0][0]["text"] == "new language"
    assert len(state["calls"]) == 3


def test_endpoint_change_gets_separate_cache_without_storing_url_credentials(mocked_pipeline, monkeypatch):
    state = mocked_pipeline
    state["duration"] = 5
    state["results"] = [{"segments": [speech(text="official")]}, {"segments": [speech(text="configured endpoint")]}]
    assert run(state)[0][0]["text"] == "official"
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/private-endpoint-secret/v1")
    assert run(state)[0][0]["text"] == "configured endpoint"
    assert len(state["calls"]) == 2
    for path in state["cache"].glob("*/manifest.json"):
        assert "private-endpoint-secret" not in path.read_text()


def test_tampered_manifest_and_response_fail_before_network(mocked_pipeline):
    state = mocked_pipeline
    state["duration"] = 5
    state["results"] = [{"segments": [speech()]}]
    run(state)
    path, saved = manifest(state)
    saved["config"]["language"] = "other"
    write_json(path, saved)
    with pytest.raises(ContentError, match="配置不符"):
        run(state)
    saved["config"]["language"] = "zh"
    write_json(path, saved)
    (path.parent / "transcription.json").write_text('{"segments": []}')
    with pytest.raises(ContentError, match="完整转写缓存校验失败"):
        run(state)
    assert len(state["calls"]) == 1


def test_interruption_after_response_write_does_not_repeat_billable_request(mocked_pipeline, monkeypatch):
    state = mocked_pipeline
    state["duration"] = 1800
    state["results"] = [{"segments": [speech()]}, {"segments": [speech("voice-0001")]}]
    original = module._add_references
    monkeypatch.setattr(module, "_add_references", lambda *args: (_ for _ in ()).throw(ContentError("模拟本地中断")))
    with pytest.raises(ContentError, match="模拟本地中断"):
        run(state)
    assert len(state["calls"]) == 1
    monkeypatch.setattr(module, "_add_references", original)
    run(state)
    assert len(state["calls"]) == 2


def test_corrupt_aac_is_rejected_before_conversion_publish_or_upload(mocked_pipeline, monkeypatch, tmp_path):
    source = tmp_path / "corrupt.aac"
    subprocess.run([audio_chunks.ffmpeg_binary(), "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=44100",
                    "-c:a", "aac", "-f", "adts", str(source)], check=True, capture_output=True)
    data = bytearray(source.read_bytes())
    frames, position = [], 0
    while position < len(data):
        assert data[position] == 0xff and data[position + 1] & 0xf6 == 0xf0
        length = ((data[position + 3] & 3) << 11) | (data[position + 4] << 3) | (data[position + 5] >> 5)
        frames.append((position, length))
        position += length
    start, length = frames[len(frames) // 2]
    # Preserve the ADTS header and corrupt one real AAC packet mid-recording.
    data[start + 7:start + length] = b"\xff" * (length - 7)
    source.write_bytes(data)

    destination = tmp_path / "existing.mp3"
    destination.write_bytes(b"previous successful conversion")
    with pytest.raises(ContentError, match="音频处理失败"):
        audio_chunks.prepare_audio(source, destination)
    assert destination.read_bytes() == b"previous successful conversion"

    state = mocked_pipeline
    state["source"] = source
    monkeypatch.setattr(module, "prepare_audio", audio_chunks.prepare_audio)
    with pytest.raises(ContentError, match="音频处理失败"):
        run(state)
    assert not state["calls"]
    assert not list(state["cache"].glob("*/audio.mp3"))
    assert not list(state["cache"].glob("*/manifest.json"))


def test_real_ffmpeg_duration_silence_and_accurate_small_slices(tmp_path):
    source = tmp_path / "tone-and-silence.wav"
    samples = bytearray()
    for index in range(6 * 16000):
        t = index / 16000
        amplitude = 0 if 2 <= t < 3 else int(9000 * math.sin(2 * math.pi * 440 * t))
        samples.extend(amplitude.to_bytes(2, "little", signed=True))
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(samples)
    encoded = audio_chunks.prepare_audio(source, tmp_path / "audio.mp3")
    analysis = audio_chunks.analyze_audio(encoded)
    assert analysis["duration"] == pytest.approx(6, abs=0.02)
    assert any(start <= 2.05 and end >= 2.95 for start, end in analysis["silences"])
    chunks = audio_chunks.plan_chunks(analysis["duration"], analysis["silences"], max_seconds=4)
    assert chunks[0]["boundary"] == "silence"
    paths = [audio_chunks.extract_audio(encoded, tmp_path / f"part-{index}.mp3", c["start"], c["end"])
             for index, c in enumerate(chunks)]
    measured = [audio_chunks.analyze_audio(path)["duration"] for path in paths]
    assert sum(measured) == pytest.approx(6, abs=0.03)
    reference = audio_chunks.extract_audio(encoded, tmp_path / "reference.wav", 3.2, 5.8, reference=True)
    with wave.open(str(reference), "rb") as stream:
        assert stream.getnframes() / stream.getframerate() == pytest.approx(2.6, abs=0.002)
