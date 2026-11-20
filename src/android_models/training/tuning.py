from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from typing import Any

from ..contracts import ContractError, canonical_hash


def bounded_grid(search: dict[str, list[Any]], max_trials: int) -> Iterator[dict[str, Any]]:
    """Deterministic bounded grid; callers persist every yielded trial and failure."""
    if max_trials <= 0:
        raise ContractError("max_trials must be positive")
    keys = sorted(search)
    for index, values in enumerate(itertools.product(*(search[key] for key in keys))):
        if index >= max_trials:
            break
        yield dict(zip(keys, values, strict=True))


def run_trials(
    search: dict[str, list[Any]],
    max_trials: int,
    objective: Callable[[dict[str, Any]], float],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trials: list[dict[str, Any]] = []
    for params in bounded_grid(search, max_trials):
        record: dict[str, Any] = {"params": params, "trial_hash": canonical_hash(params)}
        try:
            record["metric"] = float(objective(params))
            record["status"] = "complete"
        except Exception as exc:  # noqa: BLE001 - failed/OOM trials are audit records
            record["status"] = "failed"
            record["reason"] = f"{type(exc).__name__}: {exc}"
        trials.append(record)
    completed = [trial for trial in trials if trial["status"] == "complete"]
    if not completed:
        raise ContractError("all tuning trials failed")
    return max(completed, key=lambda trial: trial["metric"]), trials
