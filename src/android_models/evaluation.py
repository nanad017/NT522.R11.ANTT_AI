from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import ContractError
from .metrics import binary_metrics
from .registry import write_json_atomic


def evaluate_predictions(
    predictions_path: str | Path,
    output: str | Path,
    split: str = "test",
) -> dict[str, Any]:
    rows = pq.read_table(predictions_path).to_pylist()
    selected = [row for row in rows if row["split"] == split and row["coverage_status"] == "included"]
    if not selected:
        raise ContractError(f"no included predictions for split={split}")
    score_field = "probability" if selected[0].get("probability") is not None else "score"
    thresholds = {float(row["threshold"]) for row in selected}
    if len(thresholds) != 1:
        raise ContractError("prediction rows contain inconsistent thresholds")
    threshold = thresholds.pop()
    aggregate = binary_metrics(
        np.asarray([row["label"] for row in selected]),
        np.asarray([row[score_field] for row in selected]),
        threshold,
    )
    by_year: list[dict[str, Any]] = []
    for year in sorted({int(row["year"]) for row in selected}):
        group = [row for row in selected if int(row["year"]) == year]
        values = binary_metrics(
            np.asarray([row["label"] for row in group]),
            np.asarray([row[score_field] for row in group]),
            threshold,
        )
        by_year.append({"year": year, **{k: v for k, v in values.items() if k != "confusion_matrix"}})
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    report = {
        "split": split,
        "included": len(selected),
        "source_rows": len(rows),
        "coverage": len(selected) / len([row for row in rows if row["split"] == split]),
        "metrics": aggregate,
    }
    write_json_atomic(output_path / "metrics.json", report)
    pq.write_table(pa.Table.from_pylist(by_year), output_path / "metrics-by-year.parquet")
    return report

