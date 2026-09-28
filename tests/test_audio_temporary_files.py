"""Audio conversion owns only its unique temporary output, on every exit path."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from podcast_scribe import audio_chunks
from podcast_scribe.model import ContentError


def convert(operation, source, target):
    if operation == "prepare":
        return audio_chunks.prepare_audio(source, target)
    return audio_chunks.extract_audio(source, target, 0, 1)


@pytest.mark.parametrize("operation", ["prepare", "extract"])
@pytest.mark.parametrize("failure", [False, True])
def test_temporary_output_is_removed_without_touching_source_or_other_files(
        tmp_path, monkeypatch, operation, failure):
    source, destination = tmp_path / "original.wav", tmp_path / "audio.mp3"
    source.write_bytes(b"caller's original recording")
    destination.write_bytes(b"previous successful output")
    unrelated = tmp_path / "audio.tmp.mp3"
    unrelated.write_bytes(b"another operation's temporary file")
    temporary = []

    def process(arguments, **kwargs):
        path = Path(arguments[-1])
        temporary.append(path)
        path.write_bytes(b"new converted audio")
        if failure:
            raise ContentError("simulated interrupted ffmpeg")

    monkeypatch.setattr(audio_chunks, "_run", process)
    if failure:
        with pytest.raises(ContentError, match="interrupted"):
            convert(operation, source, destination)
        assert destination.read_bytes() == b"previous successful output"
    else:
        assert convert(operation, source, destination) == destination
        assert destination.read_bytes() == b"new converted audio"
    assert len(temporary) == 1 and not temporary[0].exists()
    assert temporary[0] != unrelated
    assert source.read_bytes() == b"caller's original recording"
    assert unrelated.read_bytes() == b"another operation's temporary file"


@pytest.mark.parametrize("operation", ["prepare", "extract"])
def test_concurrent_conversions_use_distinct_temporary_files(tmp_path, monkeypatch, operation):
    source, destination = tmp_path / "original.wav", tmp_path / "audio.mp3"
    source.write_bytes(b"original")
    started = Barrier(2)
    temporary = []

    def process(arguments, **kwargs):
        path = Path(arguments[-1])
        temporary.append(path)
        path.write_bytes(b"converted")
        started.wait(timeout=5)
        assert path.is_file()

    monkeypatch.setattr(audio_chunks, "_run", process)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(convert, operation, source, destination) for _ in range(2)]
        assert all(result.result(timeout=10) == destination for result in results)
    assert len(set(temporary)) == 2
    assert all(not path.exists() for path in temporary)
    assert source.read_bytes() == b"original"
    assert destination.read_bytes() == b"converted"


def test_oversize_slice_removes_temporary_output_and_preserves_previous_result(tmp_path, monkeypatch):
    source, destination = tmp_path / "original.wav", tmp_path / "slice.mp3"
    source.write_bytes(b"original")
    destination.write_bytes(b"previous result")
    monkeypatch.setattr(audio_chunks, "MAX_UPLOAD_BYTES", 1)
    monkeypatch.setattr(audio_chunks, "_run", lambda args, **kwargs: Path(args[-1]).write_bytes(b"oversized"))
    with pytest.raises(ContentError, match="超过"):
        audio_chunks.extract_audio(source, destination, 0, 1)
    assert set(tmp_path.iterdir()) == {source, destination}
    assert destination.read_bytes() == b"previous result"


@pytest.mark.parametrize("operation", ["prepare", "extract"])
def test_conversion_cannot_replace_the_callers_original_file(tmp_path, monkeypatch, operation):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"original")
    monkeypatch.setattr(audio_chunks, "_run", lambda *args, **kwargs: pytest.fail("must not start ffmpeg"))
    with pytest.raises(ContentError, match="源文件"):
        convert(operation, source, source)
    assert source.read_bytes() == b"original"
