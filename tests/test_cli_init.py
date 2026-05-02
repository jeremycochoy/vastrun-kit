from __future__ import annotations

import tomllib
from pathlib import Path

from typer.testing import CliRunner

from vastrun_kit.cli.init import app

runner = CliRunner()


def _strip_comments(text: str) -> str:
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )


def test_init_refuses_when_file_exists(tmp_path: Path) -> None:
    (tmp_path / ".vastrun.toml").write_text("[vast]\n")
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "already exists" in result.stderr
    assert ".vastrun.toml" in result.stderr


def test_init_creates_template(tmp_path: Path) -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert (tmp_path / ".vastrun.toml").is_file()
    assert "Created .vastrun.toml" in result.stdout


def test_init_template_strips_to_valid_toml(tmp_path: Path) -> None:
    runner.invoke(app, [])
    raw = (tmp_path / ".vastrun.toml").read_text()
    stripped = _strip_comments(raw)
    # Stripping comments must yield valid TOML — empty or with [vast] block.
    tomllib.loads(stripped)


def test_init_emits_env_tip_when_missing(tmp_path: Path) -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "VASTAI_API_TOKEN" in result.stdout
    assert ".env" in result.stdout


def test_init_emits_pyproject_warning_when_missing(tmp_path: Path) -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "pyproject.toml" in result.stdout or "Python project root" in result.stdout
    assert "Warning" in result.stdout


def test_init_quiet_when_env_and_pyproject_present(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("VASTAI_API_TOKEN=x\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Created .vastrun.toml" in result.stdout
    assert "VASTAI_API_TOKEN" not in result.stdout
    assert "Warning" not in result.stdout
