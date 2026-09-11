"""End-to-end tests for artifact secrecy (HSDLC-037).

Verifies: synthetic secrets in environment variables, source text, test output,
and URLs with credentials do not appear in persisted summaries.
Raw prompt persistence is confirmed disabled by default.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from helpers.repositories import create_spec_repo

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import CommandProfile


def _scan_all_artifacts_for_secrets(repo: Path, secrets: list[str]) -> list[str]:
    """Scan every artifact (JSON and patch) under .hybrid_sdlc/runs for secrets."""
    found = []
    runs_dir = repo / ".hybrid_sdlc" / "runs"
    if not runs_dir.exists():
        return found
    for artifact in runs_dir.rglob("*"):
        if artifact.is_file():
            content = artifact.read_text(encoding="utf-8", errors="replace")
            for secret in secrets:
                if secret in content:
                    found.append(f"{artifact.name}: found '{secret}'")
    return found


def _ensure_run_completed(repo: Path, expected_attempts: int = 1) -> dict:
    """Verify that a run record exists and return the latest record."""
    runs_dir = repo / ".hybrid_sdlc" / "runs"
    json_files = sorted(runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    assert len(json_files) >= expected_attempts, (
        f"Expected at least {expected_attempts} JSON artifact(s), found {len(json_files)}"
    )
    return json.loads(json_files[-1].read_text(encoding="utf-8"))


def test_env_secret_not_in_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    fake_aider = helpers / "fake.py"
    fake_aider.write_text(
        "from pathlib import Path\nPath('solution.py').write_text('def f(): return 1\\n')\n",
        encoding="utf-8",
    )

    # Inject actual secrets into the environment
    monkeypatch.setenv("DANGEROUS_KEY", "sk-live-dagger-xxxx")
    monkeypatch.setenv("AUTH_TOKEN", "supersecretkey123")

    output_script = helpers / "output_secrets.py"
    output_script.write_text(
        "import os\n"
        'print("KEY=" + os.environ.get("DANGEROUS_KEY", "MISSING"))\n'
        'print("TOKEN=" + os.environ.get("AUTH_TOKEN", "MISSING"))\n'
        "import sys; sys.exit(0)\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="leaky_test",
        argv=[Path(sys.executable).name, str(output_script)],
        cwd=".",
        timeout_seconds=10,
        env_allowlist=["DANGEROUS_KEY", "AUTH_TOKEN"],
    )

    run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-ENV-SECRET",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    record = _ensure_run_completed(repo)
    assert record["status"] == "success"
    assert len(record.get("attempts", [])) >= 1

    secrets_to_check = [
        "sk-live-dagger-xxxx",
        "supersecretkey123",
    ]
    found = _scan_all_artifacts_for_secrets(repo, secrets_to_check)
    assert found == [], f"Secrets leaked into artifacts: {found}"


def test_source_text_secret_not_in_artifacts(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    fake_aider = helpers / "fake.py"
    fake_aider.write_text(
        "from pathlib import Path\nPath('output.py').write_text('# key=supersecret123\\n')\n",
        encoding="utf-8",
    )

    # Baseline: pass when output.py is absent (no Aider edit yet).
    # After Aider creates output.py, the test prints the secret-bearing content and fails.
    test_script = helpers / "report.py"
    test_script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "p = Path('output.py')\n"
        "if not p.exists(): sys.exit(0)\n"
        "content = p.read_text()\n"
        "print(f'Content has: {content.strip()}')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="source_test",
        argv=[Path(sys.executable).name, str(test_script)],
        cwd=".",
        timeout_seconds=10,
    )

    run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-SOURCE-SECRET",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    # Verify Aider ran and the secret-bearing file was created.
    output_py = repo / "output.py"
    assert output_py.exists(), "Aider should have created output.py with the secret"
    assert "supersecret123" in output_py.read_text()

    # Verify run artifacts were recorded.
    record = _ensure_run_completed(repo)
    assert len(record.get("attempts", [])) >= 1

    # Scan all artifacts for the secret.
    found = _scan_all_artifacts_for_secrets(repo, ["supersecret123"])
    assert found == [], f"Secret leaked from source into artifacts: {found}"


def test_url_credential_not_in_artifacts(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    fake_aider = helpers / "fake.py"
    fake_aider.write_text(
        "from pathlib import Path; "
        "Path('.aider_marker').write_text('run', encoding='utf-8'); "
        "print('[fake-aider] done')\n",
        encoding="utf-8",
    )

    test_script = helpers / "url_test.py"
    test_script.write_text(
        "print('Accessing https://user:password@example.com/api')\nimport sys; sys.exit(0)\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="url_test",
        argv=[Path(sys.executable).name, str(test_script)],
        cwd=".",
        timeout_seconds=10,
    )

    run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-URL-SECRET",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    record = _ensure_run_completed(repo)
    assert record["status"] == "success"
    assert len(record.get("attempts", [])) >= 1

    found = _scan_all_artifacts_for_secrets(repo, ["password@example.com"])
    assert found == [], f"URL credential leaked into artifacts: {found}"


def test_token_pattern_not_in_artifacts(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    fake_aider = helpers / "fake.py"
    fake_aider.write_text(
        "from pathlib import Path; "
        "Path('.aider_marker').write_text('run', encoding='utf-8'); "
        "print('[fake-aider] done')\n",
        encoding="utf-8",
    )

    test_script = helpers / "token_test.py"
    test_script.write_text(
        "print('token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12')\n"
        "print('bearer=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9')\n"
        "import sys; sys.exit(0)\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="token_test",
        argv=[Path(sys.executable).name, str(test_script)],
        cwd=".",
        timeout_seconds=10,
    )

    run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-TOKEN-SECRET",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    record = _ensure_run_completed(repo)
    assert record["status"] == "success"
    assert len(record.get("attempts", [])) >= 1

    found = _scan_all_artifacts_for_secrets(
        repo,
        [
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        ],
    )
    assert found == [], f"Token leaked into artifacts: {found}"


def test_persisted_record_contains_redaction_markers(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    fake_aider = helpers / "quick.py"
    fake_aider.write_text(
        "from pathlib import Path\nPath('solution.py').write_text('def f(): return 1\\n')\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="verify",
        argv=[
            Path(sys.executable).name,
            "-c",
            "import os, sys\n"
            'if not os.path.exists("solution.py"): sys.exit(0)\n'
            'print("secret=hidden_value_xyz")',
        ],
        cwd=".",
        timeout_seconds=10,
    )

    run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-REDACTION-MARKERS",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    runs_dir = repo / ".hybrid_sdlc" / "runs"
    json_files = sorted(runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    assert len(json_files) >= 1

    run_record_path = json_files[-1]
    record = json.loads(run_record_path.read_text(encoding="utf-8"))
    assert record["status"] == "success"
    assert len(record.get("attempts", [])) >= 1
    attempt = record["attempts"][0]
    stdout = attempt.get("test_stdout_summary", "")
    assert "hidden_value_xyz" not in stdout
    assert "[REDACTED]" in stdout


def test_raw_prompt_not_persisted(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    fake_aider = helpers / "fake.py"
    fake_aider.write_text(
        "from pathlib import Path; "
        "Path('.aider_marker').write_text('run', encoding='utf-8'); "
        "print('[fake-aider] done')\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="basic",
        argv=[Path(sys.executable).name, "-c", "print('ok')"],
        cwd=".",
        timeout_seconds=10,
    )

    run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-NO-PROMPT",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        task_instruction="IMPLEMENT_SECRET_INSTRUCTION_DO_NOT_LEAK",
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    # Verify a run record was created.
    record = _ensure_run_completed(repo)
    assert record["status"] == "success"

    runs_dir = repo / ".hybrid_sdlc" / "runs"
    for json_file in runs_dir.glob("*.json"):
        content = json_file.read_text(encoding="utf-8")
        assert "IMPLEMENT_SECRET_INSTRUCTION" not in content, (
            f"Raw prompt instruction found in {json_file.name}"
        )


def test_nested_json_secret_redacted(tmp_path: Path) -> None:
    from hybrid_sdlc.artifacts import redact_secrets

    payload = {
        "env": {"API_KEY": "sk-abcdefghij1234567890"},
        "url": "https://admin:password123@api.example.com/v1",
        "nested": {
            "auth": "Bearer token-abc-xyz-789",
            "details": "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12",
        },
    }

    redacted = redact_secrets(str(payload))
    assert "sk-abcdefghij1234567890" not in redacted
    assert "password123" not in redacted
    assert "Bearer token-abc-xyz-789" not in redacted
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12" not in redacted
    assert "REDACTED" in redacted
