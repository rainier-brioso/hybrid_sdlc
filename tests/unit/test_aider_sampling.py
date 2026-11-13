from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hybrid_sdlc.aider_runner import _temporary_model_settings
from hybrid_sdlc.config import AiderSampling, ToolkitConfig, load_config


@pytest.mark.parametrize(
    "values",
    [
        {"temperature": True},
        {"temperature": "1.0"},
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"temperature": float("nan")},
        {"top_p": 0},
        {"top_p": 1.1},
        {"top_k": True},
        {"top_k": 65},
        {"repetition_penalty": 0},
        {"repetition_penalty": float("inf")},
        {"penalty_last_n": 0},
        {"repeat_penalty": 1.05},
        {"unknown": 1},
    ],
)
def test_sampling_rejects_invalid_or_unknown_values(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ToolkitConfig(aider_sampling=values)


def test_sampling_toml_load_and_default_omission(tmp_path: Path) -> None:
    assert ToolkitConfig().aider_sampling is None
    (tmp_path / "hybrid_sdlc.toml").write_text(
        "[aider_sampling]\ntemperature = 1.0\nrepetition_penalty = 1.05\n", encoding="utf-8"
    )
    sampling = load_config(tmp_path).aider_sampling
    assert sampling == AiderSampling(temperature=1.0, repetition_penalty=1.05)
    with _temporary_model_settings(None, None) as path:
        assert path is None
    with _temporary_model_settings(None, None, AiderSampling()) as path:
        assert path is None


def test_sampling_merges_provider_body_with_reasoning_without_overriding_budget() -> None:
    sampling = AiderSampling(
        temperature=1.0,
        top_p=0.95,
        top_k=20,
        repetition_penalty=1.05,
        penalty_last_n=256,
    )
    with _temporary_model_settings(16384, 1024, sampling) as path:
        assert path is not None
        params = json.loads(path.read_text(encoding="utf-8"))[0]["extra_params"]
        assert params == {
            "max_tokens": 16384,
            "temperature": 1.0,
            "top_p": 0.95,
            "extra_body": {
                "reasoning_budget_tokens": 1024,
                "top_k": 20,
                "repetition_penalty": 1.05,
                "penalty_last_n": 256,
            },
        }
    assert not path.exists()


def test_sampling_only_file_and_zero_temperature_are_explicit() -> None:
    with _temporary_model_settings(None, None, AiderSampling(temperature=0.0)) as path:
        assert path is not None
        assert json.loads(path.read_text())[0]["extra_params"] == {"temperature": 0.0}


def test_sampling_settings_cleanup_on_failed_launch() -> None:
    with pytest.raises(RuntimeError):
        with _temporary_model_settings(None, None, AiderSampling(repetition_penalty=1.05)) as path:
            assert path is not None
            raise RuntimeError("synthetic launch failure")
    assert not path.exists()
