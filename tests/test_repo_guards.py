"""Автотесты защиты репозитория: CSV, share/, .env и ключи не должны попасть в git."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git недоступен или это не git-репозиторий: проверка файлов в индексе невозможна")
    return [p for p in out.decode("utf-8").split("\0") if p]


def test_import_package():
    import purchasing

    assert purchasing.__name__ == "purchasing"


def test_no_csv_tracked():
    csv = [p for p in tracked_files() if p.lower().endswith(".csv")]
    assert csv == [], f"CSV в git: {csv}"


def test_no_share_or_env_tracked():
    bad = [
        p
        for p in tracked_files()
        if p.startswith("share/") or p == ".env" or (p.startswith(".env.") and p != ".env.example")
    ]
    assert bad == [], f"Данные или .env в git: {bad}"


def test_gitignore_has_rules():
    lines = {l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    for rule in ("*.csv", "share/", ".env"):
        assert rule in lines, f"в .gitignore нет правила {rule}"


def test_env_example_has_only_names():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    seen = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        assert sep == "=" and re.fullmatch(r"[A-Z][A-Z0-9_]*", name), f"не пара ИМЯ=: {name}"
        seen += 1
        if name == "DATA_DIR":
            assert value == "share"
        else:
            assert value == "", f"в .env.example у {name} непустое значение"
    assert seen > 0


def test_claude_md_has_facts_rule():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.splitlines()[0].strip() == "@AGENTS.md"
    assert "facts.json" in text and "только" in text.lower()
    assert re.search(r"цифры[^.\n]*только[^.\n]*facts\.json", text, re.IGNORECASE)


def test_precommit_and_gitleaks_configs_exist():
    pre = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "gitleaks" in pre
    assert (ROOT / ".gitleaks.toml").is_file()
