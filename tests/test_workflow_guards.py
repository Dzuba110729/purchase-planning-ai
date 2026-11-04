"""Защита workflow: нет запуска от чужих pull request, действия закреплены по хешу."""

import re
from pathlib import Path

WORKFLOWS = sorted(Path(".github/workflows").glob("*.yml"))


def test_every_action_is_pinned_by_commit_hash():
    for path in WORKFLOWS:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.search(r"uses:\s*(\S+)", line)
            if match:
                assert re.search(r"@[0-9a-f]{40}$", match.group(1)), f"{path}: {match.group(1)}"


def test_secret_workflows_do_not_run_for_pull_requests():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        if "secrets." in text:
            assert "pull_request" not in text.split("jobs:")[0], path.name
            assert "permissions:" in text, path.name


def test_run_workflow_triggers_and_artifacts():
    text = Path(".github/workflows/run.yml").read_text(encoding="utf-8")
    assert 'cron: "30 4 * * *"' in text and "workflow_dispatch:" in text
    assert "upload-artifact" not in text
    assert "DASHBOARD_NV_BOT" in text
