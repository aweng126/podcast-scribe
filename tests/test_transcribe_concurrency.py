"""Deterministic cross-process tests; all transcription calls stay local."""
import hashlib
import json
import multiprocessing
from pathlib import Path

import pytest

from podcast_scribe import transcribe as module
from podcast_scribe.cli import main
from podcast_scribe.model import ContentError, write_json


def _work_directory(source, cache, language="zh"):
    source_hash = module._sha256(source)
    config = module._configuration(language)
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    return cache / hashlib.sha256(f"{source_hash}:{config_hash}".encode()).hexdigest(), config


def _seed_pending_cache(source, cache):
    work, config = _work_directory(source, cache)
    work.mkdir(parents=True)
    audio = work / "audio.mp3"
    audio.write_bytes(b"mock converted audio, never uploaded")
    write_json(work / "manifest.json", {
        "schema_version": module.CACHE_VERSION, "source_sha256": module._sha256(source),
        "config": config, "duration": 5, "audio_sha256": module._sha256(audio),
        "chunks": [{"id": "chunk-0001", "start": 0, "end": 5, "boundary": "end", "status": "pending"}],
        "status": "in_progress", "speaker_references": [],
    })
    return work


def _run_mock_cli(argv, entered, release, calls, results):
    """Spawn-safe worker: stand in for the SDK, and hold the first request open."""
    import openai
    from podcast_scribe import transcribe
    from podcast_scribe.cli import main as cli_main

    class OfflineClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def request(*args):
        with calls.get_lock():
            calls.value += 1
        entered.set()
        if not release.wait(10):
            raise AssertionError("test request was not released")
        return {"duration": 5, "segments": [{"start": 0, "end": 4, "speaker": "A", "text": "并发缓存测试"}]}

    openai.OpenAI = OfflineClient
    transcribe._request = request
    results.put(cli_main(argv))


@pytest.mark.parametrize("explicit_outputs", [False, True])
def test_same_content_different_paths_and_outputs_make_only_one_request(tmp_path, monkeypatch, explicit_outputs):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    source, alias = tmp_path / "recording.mp3", tmp_path / "renamed.mp3"
    source.write_bytes(b"identical source content, not a real recording")
    alias.write_bytes(source.read_bytes())
    cache = tmp_path / "cache"
    work = _seed_pending_cache(source, cache / "asr")
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    calls = context.Value("i", 0)
    first_results, second_results = context.Queue(), context.Queue()
    first_args = ["transcribe", str(source), "--cache", str(cache)]
    second_args = ["transcribe", str(alias), "--cache", str(cache)]
    if explicit_outputs:
        first_args += ["--output", str(tmp_path / "first.json")]
        second_args += ["--output", str(tmp_path / "second.json")]
    first = context.Process(target=_run_mock_cli, args=(first_args, entered, release, calls, first_results))
    second = context.Process(target=_run_mock_cli, args=(second_args, entered, release, calls, second_results))
    first.start()
    try:
        assert entered.wait(10), "first request never started"
        second.start()
        second.join(10)
        assert not second.is_alive(), "competing invocation must fail immediately, without waiting for the first request"
        assert second.exitcode == 0 and second_results.get(timeout=2) == 2
        assert calls.value == 1
        pending = json.loads((work / "manifest.json").read_text())
        assert pending["status"] == "in_progress"
        assert not (work / "transcription.json").exists()
    finally:
        release.set()
        first.join(10)
        for process in (first, second):
            if process.is_alive():
                process.terminate()
                process.join(5)
    assert first.exitcode == 0 and first_results.get(timeout=2) == 0
    assert calls.value == 1
    saved = json.loads((work / "manifest.json").read_text())
    assert saved["status"] == "complete" and saved["chunks"][0]["status"] == "complete"
    assert not list(work.rglob("*.tmp"))
    # The competing output can resume after the owner exits, without a key or SDK call.
    monkeypatch.delenv("OPENAI_API_KEY")
    assert main(second_args) == 0
    assert calls.value == 1


def test_cache_lock_rejects_same_work_but_allows_different_configuration(tmp_path, monkeypatch):
    source = tmp_path / "recording.mp3"
    source.write_bytes(b"mock source")
    first, _ = _work_directory(source, tmp_path / "cache")
    another_language, _ = _work_directory(source, tmp_path / "cache", "en")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    another_endpoint, _ = _work_directory(source, tmp_path / "cache")
    with module._cache_lock(first):
        with pytest.raises(ContentError, match="本次未发送转写请求"):
            with module._cache_lock(first):
                pytest.fail("same work key must not acquire another lock")
        with module._cache_lock(another_language), module._cache_lock(another_endpoint):
            pass


def test_cache_lock_is_released_after_failure(tmp_path):
    work = tmp_path / "work"
    with pytest.raises(RuntimeError, match="local failure"):
        with module._cache_lock(work):
            raise RuntimeError("local failure")
    with module._cache_lock(work):
        assert (work / ".transcribe.lock").is_file()
