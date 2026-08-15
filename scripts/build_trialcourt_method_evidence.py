#!/usr/bin/env python3
"""Build a self-hashed TrialCourt comparison to Holm's published procedure."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from finreplay.engines import holm_adjusted_p_values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("verification/evidence/trialcourt-holm-method.json"),
    )
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    implementation = repository / "src/finreplay/engines/trialcourt.py"
    raw = {"hypothesis-a": 0.01, "hypothesis-b": 0.04, "hypothesis-c": 0.03}
    expected, steps = _independent_holm_adjustment(raw)
    observed = holm_adjusted_p_values(raw, family_size=len(raw))
    if observed != expected:
        raise SystemExit("TrialCourt Holm adjustment differs from the independent procedure")
    citation = {
        "author": "Sture Holm",
        "title": "A Simple Sequentially Rejective Multiple Test Procedure",
        "journal": "Scandinavian Journal of Statistics",
        "volume": "6",
        "issue": "2",
        "pages": "65-70",
        "year": 1979,
        "doi": "10.2307/4615733",
        "canonical_url": "https://doi.org/10.2307/4615733",
    }
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "evidence_kind": "published_method_comparison",
        "method": "Holm sequentially rejective Bonferroni adjustment",
        "citation": citation,
        "citation_sha256": _hash(citation),
        "implementation_path": "src/finreplay/engines/trialcourt.py",
        "implementation_sha256": hashlib.sha256(implementation.read_bytes()).hexdigest(),
        "family_size": len(raw),
        "raw_p_values": raw,
        "independent_step_down_calculation": steps,
        "expected_adjusted_p_values": expected,
        "trialcourt_adjusted_p_values": observed,
        "exact_match": True,
        "claim_boundary": (
            "This fixture checks only TrialCourt's deterministic Holm adjusted-p-value routine "
            "against an independently expanded step-down calculation of the published 1979 "
            "procedure. It does not externally validate TrialCourt's other five attacks, prove "
            "statistical validity for a particular study, certify family selection, or establish "
            "trading, deployment, adoption, or real-world impact."
        ),
    }
    payload["receipt_sha256"] = _hash(payload)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"exact_match=true hypotheses={len(raw)} "
        f"receipt_sha256={payload['receipt_sha256']}"
    )


def _independent_holm_adjustment(
    raw: dict[str, float],
) -> tuple[dict[str, float], list[dict[str, float | int | str]]]:
    ordered = sorted(raw.items(), key=lambda item: (item[1], item[0]))
    running_max = 0.0
    adjusted: dict[str, float] = {}
    steps: list[dict[str, float | int | str]] = []
    family_size = len(ordered)
    for zero_based_rank, (hypothesis_id, p_value) in enumerate(ordered):
        multiplier = family_size - zero_based_rank
        scaled = min(1.0, multiplier * p_value)
        running_max = max(running_max, scaled)
        adjusted[hypothesis_id] = running_max
        steps.append(
            {
                "rank": zero_based_rank + 1,
                "hypothesis_id": hypothesis_id,
                "raw_p_value": p_value,
                "multiplier": multiplier,
                "scaled_p_value": scaled,
                "monotone_adjusted_p_value": running_max,
            }
        )
    return {key: adjusted[key] for key in raw}, steps


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


if __name__ == "__main__":
    main()
