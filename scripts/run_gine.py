#!/usr/bin/env python3
"""Train the three temporal GINE seeds and publish verified result artifacts."""

from __future__ import annotations

import csv
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
from tempfile import mkdtemp

import pyarrow.parquet as pq
from organize_experiments import run_row

from android_models.catalog import load_catalog
from android_models.contracts import load_config
from android_models.registry import write_json_atomic
from android_models.training.gnn import load_trained_gnn

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs/models/gine.yaml"
EXPERIMENTS = REPO / "experiments/gine"
COVERAGE = {"train": 8368, "validation": 584, "test": 903}


def train_seed(seed: int, config: dict, cohort: dict) -> dict:
    run_id = f"train-gine-fcg-seed-{seed}-{config['_config_hash'][:12]}"
    destination = EXPERIMENTS / f"seed_{seed}"
    if destination.exists():
        result = json.loads((destination / "logs/summary.json").read_text())
        if result["run_id"] != run_id:
            raise FileExistsError(f"A different run already exists at {destination}")
        print(f"Seed {seed}: using verified results at {destination}", flush=True)
        return result
    attempt = Path(mkdtemp(prefix=f"seed_{seed}-", dir=EXPERIMENTS / ".runs"))
    print(f"Seed {seed}: {attempt}", flush=True)
    with (attempt / "console.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            [
                sys.executable, "-m", "android_models.cli", "train-common-gnn",
                "--config", str(CONFIG), "--model", "gine", "--view", "fcg",
                "--seed", str(seed), "--output", str(attempt),
            ],
            cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True,
        )
    source = attempt / run_id
    _, checkpoint = load_trained_gnn(source)
    if checkpoint["model"]["kind"] != "gine" or checkpoint["model"]["edge_dim"] != 9:
        raise ValueError(f"Unexpected checkpoint architecture for seed {seed}")
    metrics = json.loads((source / "metrics-all-splits.json").read_text())
    if {split: values["n"] for split, values in metrics.items()} != COVERAGE:
        raise ValueError(f"Unexpected metric coverage for seed {seed}")
    predictions = pq.read_table(source / "predictions.parquet").to_pylist()
    predicted_cohort = {
        row["apk_sha256"]: (row["split"], row["year"], row["label"]) for row in predictions
    }
    if len(predictions) != len(cohort) or predicted_cohort != cohort:
        raise ValueError(f"Prediction cohort differs from the catalog for seed {seed}")
    years = pq.read_table(source / "metrics-by-year.parquet").to_pylist()
    expected_years = Counter(year for split, year, _ in cohort.values() if split == "test")
    if {row["year"]: row["n"] for row in years} != expected_years:
        raise ValueError(f"Unexpected test coverage by year for seed {seed}")
    staged = attempt / "organized"
    result = run_row("gine", seed, run_id, staged, source_root=attempt)
    write_json_atomic(staged / "logs/summary.json", result)
    staged.rename(destination)
    return result


def write_report(runs: list[dict]) -> None:
    metrics = ("test_pr_auc", "test_roc_auc", "test_f1", "test_mcc")
    summary = {}
    for metric in metrics:
        values = [row[metric] for row in runs]
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Nonfinite metric: {metric}")
        summary[metric] = (statistics.mean(values), statistics.pstdev(values))
    with (REPO / "results/summary-by-model.csv").open(newline="", encoding="utf-8") as handle:
        gin = next(row for row in csv.DictReader(handle) if row["model"] == "gin")
    lines = [
        "# GINE trên Function Call Graph", "",
        "Train 8.368 APK (2019–2021), validation 584 APK (2022), test 903 APK (2023–2026).",
        "Test bao phủ 903/978 APK trong manifest gốc. Validation chọn checkpoint theo PR-AUC",
        "và chọn ngưỡng theo F1. GINE dùng 9 đặc trưng cạnh.", "",
        "| Seed | Validation PR-AUC | Test PR-AUC | Test ROC-AUC | Test F1 | Test MCC | Giờ chạy |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in runs:
        scores = " | ".join(f"{row[metric]:.4f}" for metric in metrics)
        lines.append(
            f"| {row['seed']} | {row['validation_pr_auc']:.4f} | {scores} | "
            f"{row['runtime_seconds'] / 3600:.2f} |"
        )
    lines.extend([
        "", "Trung bình ± độ lệch chuẩn trên ba seed.", "",
        "| Model | PR-AUC | ROC-AUC | F1 | MCC |", "|---|---:|---:|---:|---:|",
    ])
    gine_values = [f"{mean:.4f} ± {std:.4f}" for mean, std in summary.values()]
    gin_values = [f"{float(gin[m + '_mean']):.4f} ± {float(gin[m + '_std']):.4f}" for m in metrics]
    lines.extend([f"| GINE | {' | '.join(gine_values)} |", f"| GIN | {' | '.join(gin_values)} |"])
    results = REPO / "results"
    write_json_atomic(results / "gine.json", {"runs": runs, "coverage": COVERAGE, "summary": summary})
    with (results / "GINE.md.tmp").open("w", encoding="utf-8") as report:
        report.write("\n".join(lines) + "\n")
    (results / "GINE.md.tmp").replace(results / "GINE.md")


def main() -> None:
    (EXPERIMENTS / ".runs").mkdir(parents=True, exist_ok=True)
    with (EXPERIMENTS / ".runs/workflow.lock").open("a") as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        config = load_config(CONFIG)
        source = config["data"]
        rows = load_catalog(REPO / source["catalog"], artifact_root=REPO / source["artifact_roots"][0])
        if Counter(row["split"] for row in rows if row.get("fcg_path")) != COVERAGE:
            raise ValueError("Unexpected FCG coverage in catalog")
        cohort = {row["apk_sha256"]: (row["split"], row["year"], row["label"]) for row in rows}
        with ThreadPoolExecutor(max_workers=len(config["seeds"])) as pool:
            runs = list(pool.map(lambda seed: train_seed(seed, config, cohort), config["seeds"]))
        write_report(runs)
        print("All GINE runs complete. Report: results/GINE.md", flush=True)


if __name__ == "__main__":
    main()
