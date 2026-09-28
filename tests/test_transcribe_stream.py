"""Streaming diarization is cacheable only after a validated terminal event."""
import json
from types import SimpleNamespace

import httpx
import pytest

from podcast_scribe import transcribe as module
from podcast_scribe.model import ContentError


def segment(ident="seg-1", text="第一句。", start=0, end=1, speaker="A"):
    return {"type": "transcript.text.segment", "id": ident,
            "start": start, "end": end, "speaker": speaker, "text": text}


def done(text="第一句。", **kwargs):
    return {"type": "transcript.text.done", "text": text, **kwargs}


class FakeStream:
    def __init__(self, events, *, close_error=None):
        self.events = events
        self.close_error = close_error
        self.closed = False
        self.audio = None

    def __iter__(self):
        for event in self.events:
            assert not self.audio.closed
            if isinstance(event, Exception):
                raise event
            yield event

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


@pytest.fixture
def request_state(tmp_path, monkeypatch):
    audio = tmp_path / "recording.mp3"
    audio.write_bytes(b"not real audio; never uploaded")
    state = SimpleNamespace(audio=audio, work=tmp_path, streams=[], calls=[], sleeps=[])

    def create(**kwargs):
        state.calls.append(kwargs)
        stream = state.streams.pop(0)
        stream.audio = kwargs["file"]
        return stream

    state.client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    monkeypatch.setattr(module.time, "sleep", state.sleeps.append)
    return state


def request(state, references=None):
    return module._request(state.client, state.audio, "zh", references or [], state.work, 5)


def test_stream_keeps_completed_segments_usage_and_reference_contract(request_state):
    state = request_state
    usage = {"type": "tokens", "input_tokens": 80, "output_tokens": 12, "total_tokens": 92}
    typed = SimpleNamespace(model_dump=lambda: segment())
    stream = FakeStream([{"type": "transcript.text.delta", "delta": "临时文本"}, typed,
                         segment("seg-2", "第二句。", 1.5, 4, "B"),
                         done("第一句。\n 第二句。", usage=usage)])
    state.streams = [stream]
    (state.work / "reference.wav").write_bytes(b"synthetic reference")
    result = request(state, [{"name": "voice-0001", "file": "reference.wav"}])
    assert result == {"text": "第一句。\n 第二句。", "usage": usage,
                      "segments": [{k: v for k, v in row.items() if k != "type"}
                                   for row in (segment(), segment("seg-2", "第二句。", 1.5, 4, "B"))]}
    call = state.calls[0]
    assert call["stream"] is True
    assert call["response_format"] == "diarized_json" and call["chunking_strategy"] == "auto"
    assert call["extra_body"]["known_speaker_names"] == ["voice-0001"]
    assert call["extra_body"]["known_speaker_references"][0].startswith("data:audio/wav;base64,")
    assert stream.closed and call["file"].closed and state.sleeps == []


def test_eof_without_done_retries_only_to_existing_limit_and_closes_each_stream(request_state):
    state = request_state
    streams = [FakeStream([segment()]) for _ in range(module.MAX_ATTEMPTS)]
    state.streams = streams.copy()
    with pytest.raises(ContentError, match="完成事件之前中断"):
        request(state)
    assert len(state.calls) == module.MAX_ATTEMPTS == 3
    assert state.sleeps == [1, 2]
    assert all(stream.closed and stream.audio.closed for stream in streams)


@pytest.mark.parametrize("interruption", [None, httpx.RemoteProtocolError("synthetic disconnect")])
def test_retry_discards_partial_segments_and_reuses_ids_safely(request_state, interruption):
    state = request_state
    first = FakeStream([segment(text="未完成尝试的文本"), *([interruption] if interruption else [])])
    second = FakeStream([segment(), done()])
    state.streams = [first, second]
    result = request(state)
    assert result["segments"] == [{k: v for k, v in segment().items() if k != "type"}]
    assert first.closed and second.closed and len(state.calls) == 2
    assert state.sleeps == [1]


@pytest.mark.parametrize("events", [
    [{"type": "error", "message": "provider secret must not be echoed"}],
    [{"type": "unknown.event"}],
    [{"type": "transcript.text.delta", "delta": None}],
    [segment(), segment(), done("第一句。第一句。")],
    [{k: v for k, v in segment().items() if k != "id"}, done()],
    [{k: v for k, v in segment().items() if k != "speaker"}, done()],
    [segment(text=None), done("")],
    [segment(text=" ", end=6), done("")],
    [segment(text=" ", speaker=123), done("")],
    [segment(text=" "), segment(), done()],
    [segment(text=" ", start=2, end=3), segment("seg-2"), done()],
    [segment(text=" "), done("有正文却没有带时间戳的文本")],
    [segment(), done("不同的全文。")],
    [segment(), {"type": "transcript.text.done"}],
    [done("有正文却没有已完成段落")],
    [segment(start=2, end=3), segment("seg-2", start=0, end=1), done("第一句。第一句。")],
    [segment(end=6), done()],
    [segment(start=float("nan")), done()],
])
def test_invalid_stream_is_not_retried_or_accepted(request_state, events):
    state = request_state
    stream = FakeStream(events)
    state.streams = [stream]
    with pytest.raises(ContentError) as caught:
        request(state)
    assert "provider secret" not in str(caught.value)
    assert stream.closed and stream.audio.closed
    assert len(state.calls) == 1 and state.sleeps == []


def test_done_is_terminal_and_close_failure_never_retries_success(request_state):
    state = request_state
    stream = FakeStream([segment(), done(), httpx.RemoteProtocolError("after done")],
                        close_error=httpx.RemoteProtocolError("during close"))
    state.streams = [stream]
    assert request(state)["text"] == "第一句。"
    assert stream.closed and stream.audio.closed and len(state.calls) == 1
    assert state.sleeps == []


def test_empty_completed_stream_is_valid_for_a_silent_chunk(request_state):
    state = request_state
    stream = FakeStream([done("")])
    state.streams = [stream]
    assert request(state) == {"text": "", "segments": []}
    assert stream.closed


@pytest.mark.parametrize("blank", ["", " \n\t"])
def test_blank_segments_preserve_raw_events_without_retrying(request_state, blank):
    state = request_state
    events = [segment("empty-first", blank, 0, 0.25),
              segment("spoken-first", "第一句。", 0.25, 1),
              segment("empty-middle", blank, 1, 2),
              segment("spoken-last", "第二句。", 2, 4),
              segment("empty-last", blank, 4, 5)]
    stream = FakeStream([*events, done("第一句。第二句。")])
    state.streams = [stream]
    result = request(state)
    assert result["segments"] == [{k: v for k, v in event.items() if k != "type"}
                                  for event in events]
    assert [row["text"] for row in module._rows(result, 5)] == ["第一句。", "第二句。"]
    assert stream.closed and stream.audio.closed
    assert len(state.calls) == 1 and state.sleeps == []


def test_all_blank_segments_require_matching_done(request_state):
    state = request_state
    stream = FakeStream([segment(text=" \n", end=5), done("")])
    state.streams = [stream]
    result = request(state)
    assert len(result["segments"]) == 1
    assert module._rows(result, 5) == []
    assert len(state.calls) == 1 and state.sleeps == []


def test_interrupted_stream_never_writes_success_cache_and_can_resume(request_state, monkeypatch):
    import openai

    state = request_state
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-only")
    monkeypatch.setattr(module, "MAX_ATTEMPTS", 1)

    def prepare(source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return destination

    class Client:
        def __init__(self, **kwargs): self.audio = state.client.audio
        def __enter__(self): return self
        def __exit__(self, *args): pass

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(module, "prepare_audio", prepare)
    monkeypatch.setattr(module, "analyze_audio", lambda _: {"duration": 5, "silences": []})
    state.streams = [FakeStream([segment()])]
    cache = state.work / "cache"
    with pytest.raises(ContentError, match="完成事件之前中断"):
        module.transcribe_audio(state.audio, cache)
    manifest_path = next(cache.glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["chunks"][0]["status"] == "failed"
    assert not list(cache.glob("*/chunks/*.json"))
    assert not list(cache.glob("*/transcription.json"))

    state.streams = [FakeStream([segment(), done()])]
    result = module.transcribe_audio(state.audio, cache)
    assert result[0][0]["text"] == "第一句。"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "complete" and manifest["chunks"][0]["status"] == "complete"
    monkeypatch.delenv("OPENAI_API_KEY")
    assert module.transcribe_audio(state.audio, cache) == result
    assert len(state.calls) == 2
