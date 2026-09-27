import json
import subprocess
from types import SimpleNamespace

import pytest

from podcast_scribe import doctor
from podcast_scribe.cli import main


@pytest.fixture
def isolated_environment(monkeypatch):
    """Never depend on packages, fonts, executables, or credentials on the host."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)

    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(doctor.importlib, "import_module", missing)

    def unexpected(*args, **kwargs):
        raise AssertionError("unexpected external operation")

    monkeypatch.setattr(doctor.subprocess, "run", unexpected)
    return unexpected


def _report(capsys):
    captured = capsys.readouterr()
    assert not captured.err
    return json.loads(captured.out)


def test_default_reports_missing_dependencies_and_returns_nonzero(isolated_environment, capsys):
    assert main(["doctor"]) == 2
    report = _report(capsys)
    assert report["required"] == list(doctor.CAPABILITIES)
    assert report["ready"] is False
    assert report["network"] == "not tested"
    assert "未调用 API" in report["cloud_transcription"]
    for capability in ("import", "markdown", "site"):
        assert report["capabilities"][capability] == {"ready": True, "issues": []}
    for capability in ("pdf", "transcribe", "ingest", "inspect"):
        result = report["capabilities"][capability]
        assert result["ready"] is False
        assert all(issue["code"] and issue["message"] and issue["remedy"] for issue in result["issues"])
    codes = {issue["code"] for issue in report["capabilities"]["ingest"]["issues"]}
    assert codes == {"openai_unavailable", "openai_api_key_missing", "ffmpeg_missing", "yt_dlp_unavailable"}


def test_local_require_does_not_check_keys_fonts_dependencies_or_binaries(isolated_environment, monkeypatch, capsys):
    monkeypatch.setenv("PODCAST_SCRIBE_FONT", "/not/a/font")
    for name in ("_dependency", "_pdf", "_ffmpeg"):
        monkeypatch.setattr(doctor, name, isolated_environment)
    assert main(["doctor", "--require", "import", "markdown", "site", "import"]) == 0
    report = _report(capsys)
    assert report["required"] == ["import", "markdown", "site"]
    assert report["ready"] is True
    assert list(report["capabilities"]) == report["required"]


def test_pdf_checks_the_exporter_font_and_handles_invalid_font(isolated_environment, monkeypatch, capsys):
    from podcast_scribe import exporters

    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: SimpleNamespace())
    called = []

    def invalid_font():
        called.append(True)
        raise RuntimeError("font is missing or does not contain Chinese glyphs")

    monkeypatch.setattr(exporters, "_pdf_font", invalid_font)
    assert main(["doctor", "--require", "pdf"]) == 2
    report = _report(capsys)
    assert called == [True]
    assert report["capabilities"]["pdf"]["issues"][0]["code"] == "chinese_font_unavailable"


@pytest.mark.parametrize(("failure", "code"), [
    (PermissionError("not executable"), "ffmpeg_not_executable"),
    (FileNotFoundError("removed between lookup and execution"), "ffmpeg_not_executable"),
    (subprocess.TimeoutExpired("ffmpeg", 5), "ffmpeg_timeout"),
    (None, "ffmpeg_failed"),
])
def test_ffmpeg_failure_is_actionable(isolated_environment, monkeypatch, capsys, failure, code):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: SimpleNamespace())
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/fake/ffmpeg")

    def fail(*args, **kwargs):
        if failure:
            raise failure
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(doctor.subprocess, "run", fail)
    assert main(["doctor", "--require", "transcribe"]) == 2
    report = _report(capsys)
    assert report["capabilities"]["transcribe"]["issues"][0]["code"] == code


def test_all_capabilities_ready_uses_local_checks_only(isolated_environment, monkeypatch, capsys):
    from podcast_scribe import exporters

    module = SimpleNamespace(OpenAI=isolated_environment, YoutubeDL=isolated_environment)
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setattr(exporters, "_pdf_font", lambda: "MockChineseFont")
    monkeypatch.setattr(doctor, "_ocr", lambda: [])
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/fake/ffmpeg")
    calls = []

    def available(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(doctor.subprocess, "run", available)
    assert main(["doctor"]) == 0
    report = _report(capsys)
    assert report["ready"] is True
    assert all(result == {"ready": True, "issues": []} for result in report["capabilities"].values())
    assert len(calls) == 1  # ingest shares its transcription checks
    command, options = calls[0]
    assert command == ["/fake/ffmpeg", "-version"]
    assert options["timeout"] == doctor.FFMPEG_TIMEOUT_SECONDS
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert "test-only-secret" not in json.dumps(report)


def test_transcribe_can_use_bundled_imageio_binary_without_running_resolver(isolated_environment, monkeypatch, tmp_path, capsys):
    package = tmp_path / "imageio_ffmpeg"
    binary = package / "binaries" / "ffmpeg-test-platform"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake binary; subprocess is mocked")
    imageio = SimpleNamespace(__file__=str(package / "__init__.py"), get_ffmpeg_exe=isolated_environment)
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: imageio if name == "imageio_ffmpeg" else SimpleNamespace())
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    calls = []

    def available(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(doctor.subprocess, "run", available)
    assert main(["doctor", "--require", "transcribe"]) == 0
    assert _report(capsys)["ready"] is True
    assert calls == [[str(binary), "-version"]]


def test_inspect_needs_no_api_key_font_or_ffmpeg(isolated_environment, monkeypatch, capsys):
    imports = []

    def load(name):
        imports.append(name)
        return SimpleNamespace()

    monkeypatch.setattr(doctor.importlib, "import_module", load)
    monkeypatch.setattr(doctor, "_pdf", isolated_environment)
    monkeypatch.setattr(doctor, "_ffmpeg", isolated_environment)
    assert main(["doctor", "--require", "inspect"]) == 0
    assert _report(capsys)["ready"] is True
    assert imports == ["yt_dlp"]


def test_diagnostic_exceptions_never_print_api_key(isolated_environment, monkeypatch, capsys):
    from podcast_scribe import exporters

    secret = "sk-test-do-not-print-this-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/fake/ffmpeg")

    def dependency(name):
        if name == "reportlab":
            return SimpleNamespace()
        raise RuntimeError(secret)

    def unsafe_error(*args, **kwargs):
        raise PermissionError(secret)

    monkeypatch.setattr(doctor.importlib, "import_module", dependency)
    monkeypatch.setattr(exporters, "_pdf_font", unsafe_error)
    monkeypatch.setattr(doctor.subprocess, "run", unsafe_error)
    assert main(["doctor"]) == 2
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert json.loads(captured.out)["ready"] is False


def test_whitespace_key_is_not_configured(isolated_environment, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "  \n ")
    assert main(["doctor", "--require", "transcribe"]) == 2
    result = _report(capsys)["capabilities"]["transcribe"]
    assert any(issue["code"] == "openai_api_key_missing" for issue in result["issues"])


def test_cli_rejects_unknown_capability():
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--require", "unknown"])
    assert exc.value.code == 2
