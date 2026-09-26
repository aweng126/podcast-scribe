"""The skill launcher chooses an environment without changing the workspace."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


LAUNCHER = Path(__file__).resolve().parents[1] / "skills/podcast-scribe/scripts/run.sh"


@pytest.fixture
def launch(tmp_path):
    skill = tmp_path / "skill with spaces"
    work = tmp_path / "task with spaces"
    (skill / "scripts").mkdir(parents=True)
    work.mkdir()
    script = skill / "scripts/run.sh"
    shutil.copyfile(LAUNCHER, script)
    (skill / "scripts/podcast_scribe.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path('data').mkdir(exist_ok=True)\n"
        "print(json.dumps({'cwd': str(Path.cwd()), 'args': sys.argv[1:], "
        "'runtime': os.environ.get('TEST_RUNTIME')}))\n", encoding="utf-8")

    def run(*args, override=None, search_path=None):
        env = {key: value for key, value in os.environ.items()
               if key not in {"VIRTUAL_ENV", "PODCAST_SCRIBE_PYTHON"}}
        if override is not None:
            env["PODCAST_SCRIBE_PYTHON"] = str(override)
        if search_path is not None:
            env["PATH"] = str(search_path)
        return subprocess.run(["/bin/bash", str(script), *args], cwd=work, env=env,
                              capture_output=True, text=True, timeout=15)

    return skill, work, run


def fake_python(path, label, score=4):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '#!/bin/bash\n'
        'if [[ "$1" == "-c" && "$2" == *sys.prefix* ]]; then\n'
        f'  exec {shlex.quote(sys._base_executable)} "$@"\nfi\n'
        'if [[ "$1" == "-c" ]]; then\n'
        f'  printf "%s\\n" {score}\n  exit 0\nfi\n'
        f'export TEST_RUNTIME={shlex.quote(label)}\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


def test_launcher_preserves_workspace_and_passes_arguments_without_shell_evaluation(launch):
    skill, work, run = launch
    fake_python(skill / ".venv/bin/python", "installed-skill")
    value = "https://www.bilibili.com/video/BV1XNtJ6UEmm?p=2&literal=$(touch injected)"
    result = run("ingest", value)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output == {"cwd": str(work), "args": ["ingest", value], "runtime": "installed-skill"}
    assert (work / "data").is_dir() and not (skill / "data").exists()
    assert not (work / "injected").exists()


def test_launcher_prefers_ready_workspace_environment_to_incomplete_skill_environment(launch):
    skill, _, run = launch
    work = launch[1]
    fake_python(skill / ".venv/bin/python", "incomplete", score=1)
    fake_python(work / ".venv/bin/python", "workspace")
    result = run("doctor", "--require", "ingest", "pdf")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime"] == "workspace"


def test_launcher_respects_explicit_interpreter_without_evaluating_it(launch):
    skill, work, run = launch
    fake_python(skill / ".venv/bin/python", "default")
    override = fake_python(work / "custom environment/python", "explicit", score=0)
    result = run("status", "episode.json", override=override)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime"] == "explicit"


def test_invalid_explicit_interpreter_fails_instead_of_using_another_environment(launch):
    skill, work, run = launch
    fake_python(skill / ".venv/bin/python", "default")
    result = run("--help", override=work / "missing python")
    assert result.returncode == 2
    assert "PODCAST_SCRIBE_PYTHON" in result.stderr
    assert not (work / "data").exists()


def test_setup_reuses_ready_environment_without_calling_pip_or_cli(launch):
    skill, work, run = launch
    fake_python(skill / ".venv/bin/python", "ready")
    result = run("setup")
    assert result.returncode == 0, result.stderr
    assert "复用" in result.stdout
    assert not (work / "data").exists()


def test_setup_does_not_replace_broken_environment(launch):
    skill, work, run = launch
    (skill / ".venv").mkdir()
    marker = skill / ".venv/keep"
    marker.write_text("existing files")
    bootstrap = fake_python(work / "bootstrap/python3", "bootstrap", score=0)
    (bootstrap.parent / "dirname").symlink_to(shutil.which("dirname"))
    result = run("setup", search_path=bootstrap.parent)
    assert result.returncode == 2
    assert "可用" in result.stderr
    assert marker.read_text() == "existing files"
    assert not (skill / ".venv/bin").exists()


def test_setup_refuses_incomplete_global_override(launch):
    skill, work, run = launch
    override = fake_python(work / "bootstrap/python", "bootstrap", score=0)
    result = run("setup", override=override)
    assert result.returncode == 2
    assert "PODCAST_SCRIBE_PYTHON" in result.stderr
    assert not (skill / ".venv").exists()
    assert not (work / "data").exists()


def test_setup_creates_isolated_environment_and_installs_only_there(launch):
    skill, work, run = launch
    # Record installation routing without downloading dependencies or invoking pip.
    bootstrap = work / "bootstrap/python3"
    bootstrap.parent.mkdir()
    installer = (
        f"#!{sys.executable}\n"
        "import json, sys\nfrom pathlib import Path\n"
        "if sys.argv[1] == '-c':\n    raise SystemExit(0)\n"
        "assert sys.argv[1:5] == ['-m', 'pip', 'install', '-e']\n"
        "Path(__file__).with_name('install.json').write_text(json.dumps("
        "{'cwd': str(Path.cwd()), 'args': sys.argv[1:]}))\n"
    )
    bootstrap.write_text(
        f"#!{sys.executable}\n"
        "import sys\nfrom pathlib import Path\n"
        "if sys.argv[1] == '-c':\n    print(0)\n    raise SystemExit(0)\n"
        "assert sys.argv[1:3] == ['-m', 'venv']\n"
        "python = Path(sys.argv[3]) / 'bin/python'\n"
        "python.parent.mkdir(parents=True)\n"
        f"python.write_text({installer!r})\npython.chmod(0o755)\n",
        encoding="utf-8",
    )
    bootstrap.chmod(0o755)
    (bootstrap.parent / "dirname").symlink_to(shutil.which("dirname"))
    result = run("setup", search_path=bootstrap.parent)
    assert result.returncode == 0, result.stderr
    installed = json.loads((skill / ".venv/bin/install.json").read_text())
    assert installed == {"cwd": str(work), "args": ["-m", "pip", "install", "-e", str(skill)]}
    assert not (work / ".venv").exists()
    assert not (work / "data").exists()


def test_setup_installs_into_explicit_environment_so_subsequent_calls_use_it(launch):
    skill, work, run = launch
    python = work / "selected environment/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        "installed = Path(__file__).with_name('installed.json')\n"
        "if sys.argv[1] == '-c':\n"
        "    print(4 if installed.exists() else 0)\n    raise SystemExit(0)\n"
        "if sys.argv[1:5] == ['-m', 'pip', 'install', '-e']:\n"
        "    installed.write_text(json.dumps(sys.argv[1:]))\n    raise SystemExit(0)\n"
        "os.environ['TEST_RUNTIME'] = 'explicit'\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n", encoding="utf-8")
    python.chmod(0o755)
    setup = run("setup", override=python)
    assert setup.returncode == 0, setup.stderr
    assert json.loads(python.with_name("installed.json").read_text()) == ["-m", "pip", "install", "-e", str(skill)]
    assert not (skill / ".venv").exists()
    result = run("doctor", override=python)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime"] == "explicit"
