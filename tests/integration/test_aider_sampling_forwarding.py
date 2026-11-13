from __future__ import annotations

import sys
from pathlib import Path

from helpers.repositories import create_spec_repo

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.config import AiderSampling
from hybrid_sdlc.models import RunStatus


def test_sampling_reaches_actual_isolated_aider_subprocess(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    fake = tmp_path / "fake_aider.py"
    fake.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "p=Path(sys.argv[sys.argv.index('--model-settings-file')+1])\n"
        "params=json.loads(p.read_text())[0]['extra_params']\n"
        "assert params == {'max_tokens':16384,'temperature':1.0,'extra_body':"
        "{'reasoning_budget_tokens':1024,'repetition_penalty':1.05}}\n"
        "Path('README.md').write_text('sampling verified in worker')\n",
        encoding="utf-8",
    )
    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="SAMPLING",
        profile=CommandProfile(name="synthetic", argv=[sys.executable, "-c", "pass"]),
        resolved_test_executable=Path(sys.executable),
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="synthetic",
        aider_cmd=[sys.executable, str(fake)],
        max_retries=1,
        aider_max_tokens=16384,
        aider_reasoning_budget_tokens=1024,
        aider_sampling=AiderSampling(temperature=1.0, repetition_penalty=1.05),
    )
    assert result.status == RunStatus.SUCCESS
    assert result.final_diff is not None
    assert result.final_diff.changed_files == ["README.md"]
    assert (repo / "README.md").read_text() == "initial"
