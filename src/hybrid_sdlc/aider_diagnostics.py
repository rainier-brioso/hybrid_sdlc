"""Bounded process facts and advisory indicators, never retained model text."""

from __future__ import annotations

import math
from typing import Any

from hybrid_sdlc.processes import SubprocessResult

SCAN_CHAR_LIMIT = 64 * 1024
BYTE_COUNT_LIMIT = 40 * 1024 * 1024

# Observed Aider 0.86.2 warnings. These can also occur in untrusted model output:
# their presence is evidence of text, not an authoritative provider finish reason.
_INDICATORS = (
    ("malformed_edit_warning", "The LLM did not conform to the edit format."),
    ("output_limit_warning", "-- possibly exceeded output limit!"),
    ("context_limit_warning", "-- possibly exhausted context window!"),
)


def empty_patch_metadata(result: SubprocessResult, attempt_index: int) -> dict[str, Any]:
    """Summarize one empty-patch failure using a fixed allowlist under 1 KiB.

    Counts describe retained subprocess output, not the full provider response.
    Missing warning text never proves an error did not occur. Do not extract
    exception messages, paths, prompts, reasoning or arbitrary output fragments.
    """
    streams = (result.stdout, result.stderr)
    counts = [len(stream.encode("utf-8", errors="replace")) for stream in streams]
    # Scan streams separately so a marker cannot be invented across their join.
    prefixes = [stream[:SCAN_CHAR_LIMIT] for stream in streams]
    duration = result.duration_seconds
    return {
        "version": 1,
        "attempt_index": attempt_index,
        "exit_code": max(-(2**63), min(result.exit_code, 2**63 - 1)),
        "duration_seconds": round(max(0.0, min(duration, 1e9)), 3)
        if math.isfinite(duration)
        else None,
        "timed_out": result.timed_out,
        "cancelled": result.cancelled,
        "output_truncated": result.is_truncated,
        "retained_stdout_bytes": min(counts[0], BYTE_COUNT_LIMIT),
        "retained_stderr_bytes": min(counts[1], BYTE_COUNT_LIMIT),
        "byte_counts_capped": any(count > BYTE_COUNT_LIMIT for count in counts),
        "indicator_scan_incomplete": result.is_truncated
        or any(len(stream) > SCAN_CHAR_LIMIT for stream in streams),
        "output_indicators": [
            label for label, marker in _INDICATORS if any(marker in part for part in prefixes)
        ],
        "cause": "unknown",
        "candidate_tests_run": False,
    }
