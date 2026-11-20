#!/usr/bin/env python3
"""Copy canonical completed runs into report-friendly experiment directories."""

from __future__ import annotations

import csv
import json
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = Path("/home/rl/Data/model-artifacts/full-ml-fcg/runs")
RUNS = {
    "linear_svm": {seed: f"train-linear-svm-seed-{seed}-fcd2cd458149" for seed in (13, 42, 2026)},
    "random_forest": {
        seed: f"train-random-forest-seed-{seed}-96272aaa32bd" for seed in (13, 42, 2026)
    },
    "xgboost": {seed: f"train-xgboost-seed-{seed}-96272aaa32bd" for seed in (13, 42, 2026)},
    "gcn": {seed: f"train-gcn-fcg-seed-{seed}-96272aaa32bd" for seed in (13, 42, 2026)},
    "gat": {seed: f"train-gat-fcg-seed-{seed}-96272aaa32bd" for seed in (13, 42, 2026)},
    "gin": {seed: f"train-gin-fcg-seed-{seed}-fdac4f4c1874" for seed in (13, 42, 2026)},
}


def copy_files(source: Path, destination: Path) -> None:
    groups = {
        "checkpoints": ["checkpoint.json", "checkpoint.skops", "checkpoint.ubj", "checkpoint.safetensors"],
        "logs": ["run.json", "events.jsonl", "data.json", "fit.json", "history.json", "progress.json"],
        "metrics": [
            "metrics.json",
            "metrics-all-splits.json",
            "metrics-by-year.parquet",
            "threshold.json",
            "predictions.parquet",
            "diagnostics.json",
        ],
    }
    for group, names in groups.items():
        target = destination / group
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            path = source / name
            if path.is_file():
                shutil.copy2(path, target / name)
    (destination / "figures").mkdir(parents=True, exist_ok=True)
    (destination / "explanations").mkdir(parents=True, exist_ok=True)


def confusion_svg(path: Path, model: str, seed: int, matrix: dict) -> None:
    values = [[matrix["tn"], matrix["fp"]], [matrix["fn"], matrix["tp"]]]
    labels = [["TN", "FP"], ["FN", "TP"]]
    maximum = max(max(row) for row in values) or 1
    cells = []
    for row in range(2):
        for column in range(2):
            value = values[row][column]
            shade = 245 - int(150 * value / maximum)
            x, y = 150 + column * 180, 90 + row * 140
            cells.append(
                f'<rect x="{x}" y="{y}" width="180" height="140" fill="rgb({shade},{shade},255)" '
                'stroke="#333"/>'
                f'<text x="{x + 90}" y="{y + 58}" text-anchor="middle" font-size="22">'
                f'{labels[row][column]}</text><text x="{x + 90}" y="{y + 95}" '
                f'text-anchor="middle" font-size="30" font-weight="bold">{value}</text>'
            )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="700" height="430" viewBox="0 0 700 430">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<text x="350" y="38" text-anchor="middle" font-size="24" font-weight="bold">'
        f'{model} seed {seed} — temporal test confusion matrix</text>'
        '<text x="330" y="405" text-anchor="middle" font-size="18">Predicted label</text>'
        '<text x="35" y="230" text-anchor="middle" font-size="18" '
        'transform="rotate(-90 35 230)">True label</text>'
        '<text x="240" y="75" text-anchor="middle">Benign</text>'
        '<text x="420" y="75" text-anchor="middle">Malware</text>'
        '<text x="130" y="165" text-anchor="end">Benign</text>'
        '<text x="130" y="305" text-anchor="end">Malware</text>'
        + "".join(cells)
        + "</svg>"
    )
    path.write_text(svg)


def run_row(model: str, seed: int, run_id: str, destination: Path) -> dict:
    source = SOURCE / run_id
    run = json.loads((source / "run.json").read_text())
    if run.get("status") != "complete":
        raise RuntimeError(f"selected run is not complete: {run_id}")
    fit = json.loads((source / "fit.json").read_text())
    report = json.loads((source / "metrics.json").read_text())
    metrics = report["metrics"]
    threshold = json.loads((source / "threshold.json").read_text())
    copy_files(source, destination)
    confusion_svg(destination / "figures/confusion-matrix.svg", model, seed, metrics["confusion_matrix"])
    return {
        "model": model,
        "seed": seed,
        "run_id": run_id,
        "status": "complete",
        "created_at": run.get("created_at"),
        "completed_at": run.get("completed_at"),
        "runtime_seconds": fit.get("runtime_seconds"),
        "best_epoch": fit.get("best_epoch"),
        "validation_pr_auc": fit.get("validation_pr_auc", fit.get("best_validation_pr_auc")),
        "threshold": threshold["threshold"],
        "test_n": metrics["n"],
        "test_pr_auc": metrics["pr_auc"],
        "test_roc_auc": metrics["roc_auc"],
        "test_f1": metrics["f1"],
        "test_precision": metrics["precision"],
        "test_recall": metrics["recall_tpr"],
        "test_specificity": metrics["specificity_tnr"],
        "test_mcc": metrics["mcc"],
        "tn": metrics["confusion_matrix"]["tn"],
        "fp": metrics["confusion_matrix"]["fp"],
        "fn": metrics["confusion_matrix"]["fn"],
        "tp": metrics["confusion_matrix"]["tp"],
        "checkpoint": f"experiments/{model}/seed_{seed}/checkpoints",
    }


def main() -> None:
    rows = []
    for model, runs in RUNS.items():
        for seed, run_id in runs.items():
            rows.append(run_row(model, seed, run_id, REPO / "experiments" / model / f"seed_{seed}"))
    fields = list(rows[0])
    with (REPO / "results/all-runs.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["model"]].append(row)
    summary = []
    for model, model_rows in grouped.items():
        record: dict = {"model": model, "completed_seeds": "13,42,2026"}
        for field in ("test_pr_auc", "test_roc_auc", "test_f1", "test_mcc"):
            values = [float(row[field]) for row in model_rows]
            record[f"{field}_mean"] = statistics.mean(values)
            record[f"{field}_std"] = statistics.pstdev(values)
        summary.append(record)
    summary_fields = list(summary[0])
    with (REPO / "results/summary-by-model.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary)

    lines = [
        "# Temporal test results",
        "",
        "Protocol: train 2019–2021, validation 2022, frozen test 2023–2026.",
        "Usable cohort: 9,855/10,000; test coverage: 903/978.",
        "",
        "| Model | Seeds | PR-AUC mean ± std | ROC-AUC mean ± std | F1 mean ± std | MCC mean ± std |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['model']} | {row['completed_seeds']} | "
            f"{row['test_pr_auc_mean']:.6f} ± {row['test_pr_auc_std']:.6f} | "
            f"{row['test_roc_auc_mean']:.6f} ± {row['test_roc_auc_std']:.6f} | "
            f"{row['test_f1_mean']:.6f} ± {row['test_f1_std']:.6f} | "
            f"{row['test_mcc_mean']:.6f} ± {row['test_mcc_std']:.6f} |"
        )
    lines.extend(
        [
            "",
            "All thresholds were selected on validation only. Complete per-seed metrics, predictions,",
            "timestamps, and safe checkpoints are under `experiments/<model>/seed_<seed>/`.",
        ]
    )
    (REPO / "results/RESULTS.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"runs": len(rows), "models": sorted(grouped)}, indent=2))


if __name__ == "__main__":
    main()
