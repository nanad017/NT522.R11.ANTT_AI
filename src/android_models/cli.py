from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from .catalog import build_catalog, load_catalog, read_manifest
from .contracts import load_config
from .evaluation import evaluate_predictions
from .features.anakin import fit_anakin_features
from .features.classical import fit_vectorizer, iter_named_records, transform_records
from .leakage import audit_catalog
from .quality import all_graph_quality
from .registry import RunRegistry
from .training.anakin import train_anakin
from .training.classical import train_classical
from .training.gnn import train_common_gnn

app = typer.Typer(no_args_is_help=True, help="Leakage-safe Android malware detector pipeline")


def _project_root(config: dict[str, Any]) -> Path:
    path = Path(config["_config_path"])
    return path.parents[2] if path.parent.name == "models" else path.parent


def _resolve(config: dict[str, Any], value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (_project_root(config) / path).resolve()


def _data(config: dict[str, Any]) -> tuple[Path, list[Path], Path]:
    section = config.get("data", config)
    manifest = _resolve(config, section["manifest"])
    roots = [_resolve(config, value) for value in section.get("artifact_roots", [])]
    catalog = _resolve(config, section.get("catalog", "model-artifacts/catalog/catalog.parquet"))
    return manifest, roots, catalog


def _emit(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str))


@app.command("preflight")
def preflight(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/preflight"),
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    manifest, roots, catalog = _data(config)
    manifest_rows = read_manifest(manifest)
    report: dict[str, Any] = {
        "status": "ok_for_implementation_not_full_training",
        "manifest_rows": len(manifest_rows),
        "manifest_splits": {
            split: sum(row["split"] == split for row in manifest_rows.values())
            for split in ("train", "validation", "test")
        },
        "artifact_roots": [{"path": str(root), "exists": root.exists()} for root in roots],
        "device": "cpu",
        "dry_run": dry_run,
    }
    if catalog.exists():
        rows = load_catalog(catalog)
        report["catalog_rows"] = len(rows)
        report["usable"] = {
            view: sum(bool(row.get(f"{view}_path")) for row in rows)
            for view in ("classical", "fcg", "ccg", "gred", "sigdroid", "anakin")
        }
        if config.get("gates", {}).get("scan_graph_quality", True):
            report["graph_quality"] = all_graph_quality(rows)
        gates = config.get("gates", {})
        required_views = gates.get("required_views", ["classical", "fcg"])
        joint_usable = sum(
            all(bool(row.get(f"{view}_path")) for view in required_views) for row in rows
        )
        expected = int(gates.get("expected_samples", len(manifest_rows)))
        minimum_fraction = float(gates.get("minimum_usable_fraction", 1.0))
        report["gate"] = {
            "expected_samples": expected,
            "required_views": required_views,
            "joint_usable": joint_usable,
            "excluded": expected - joint_usable,
            "usable_fraction": joint_usable / expected if expected else 0.0,
            "minimum_usable_fraction": minimum_fraction,
            "exclusion_manifest_exists": (catalog.parent / "exclusions.parquet").exists(),
        }
        gate_ok = (
            len(rows) == expected
            and joint_usable / expected >= minimum_fraction
            and (catalog.parent / "exclusions.parquet").exists()
        )
        report["status"] = "ready_with_declared_exclusions" if gate_ok else "blocked_data_gate"
    if not dry_run:
        output = _resolve(config, output)
        output.mkdir(parents=True, exist_ok=True)
        from .registry import write_json_atomic

        write_json_atomic(output / "preflight.json", report)
    _emit(report)


@app.command("build-catalog")
def build_catalog_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path | None, typer.Option("--output")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    manifest, roots, catalog = _data(config)
    destination = _resolve(config, output) if output else catalog.parent
    if dry_run:
        _emit({"manifest": manifest, "artifact_roots": roots, "output": destination})
        return
    _emit(build_catalog(manifest, roots, destination))


@app.command("audit-leakage")
def audit_leakage_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/leakage"),
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    _, _, catalog = _data(config)
    if dry_run:
        _emit({"catalog": catalog, "output": _resolve(config, output)})
        return
    report = audit_catalog(load_catalog(catalog))
    destination = _resolve(config, output)
    destination.mkdir(parents=True, exist_ok=True)
    from .registry import write_json_atomic

    write_json_atomic(destination / "leakage-audit.json", report)
    _emit(report)


@app.command("fit-classical-features")
def fit_classical_features_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/classical-features"),
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    _, _, catalog = _data(config)
    rows = load_catalog(catalog)
    train = [row for row in rows if row["split"] == "train" and row.get("classical_path")]
    destination = _resolve(config, output)
    if dry_run:
        _emit({"fit_rows": len(train), "output": destination})
        return
    metadata = fit_vectorizer(iter_named_records(train), destination)
    _emit(metadata)


@app.command("transform-classical-features")
def transform_classical_features_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/classical-features"),
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    _, _, catalog = _data(config)
    destination = _resolve(config, output)
    rows = load_catalog(catalog)
    classical_rows = sorted(
        (row for row in rows if row.get("classical_path")), key=lambda row: row["apk_sha256"]
    )
    if dry_run:
        _emit({"transform_rows": len(classical_rows), "output": destination})
        return
    _emit(
        transform_records(
            iter_named_records(classical_rows), destination / "vectorizer.json", destination
        )
    )


@app.command("train-classical")
def train_classical_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/runs"),
    model: Annotated[str, typer.Option("--model")] = "linear_svm",
    seed: Annotated[int, typer.Option("--seed")] = 13,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    root = _project_root(config)
    destination = _resolve(config, output)
    params = config.get("classical", {}).get(model, {})
    matrix_dir = _resolve(
        config, config.get("classical", {}).get("matrix_dir", "model-artifacts/classical-features")
    )
    if dry_run:
        _emit({"model": model, "seed": seed, "matrix_dir": matrix_dir, "output": destination})
        return
    registry = RunRegistry(destination, f"train-{model}-seed-{seed}", config, root, resume)
    matrix_metadata = json.loads((matrix_dir / "matrix-metadata.json").read_text())
    registry.write(
        "data.json",
        {
            "contract": "classical-sparse-matrix-v1",
            "feature_hash": matrix_metadata["feature_hash"],
            "splits": matrix_metadata["splits"],
            "source": str(matrix_dir),
        },
    )
    try:
        result = train_classical(model, matrix_dir, registry.path, params, seed)
    except Exception as exc:
        registry.fail(f"{type(exc).__name__}: {exc}")
        raise
    registry.complete([registry.path / "predictions.parquet", registry.path / "metrics.json"])
    _emit(result)


@app.command("train-common-gnn")
def train_common_gnn_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/runs"),
    model: Annotated[str, typer.Option("--model")] = "gcn",
    view: Annotated[str, typer.Option("--view")] = "fcg",
    seed: Annotated[int, typer.Option("--seed")] = 13,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    _, roots, catalog = _data(config)
    rows = load_catalog(catalog, artifact_root=roots[0] if len(roots) == 1 else None)
    destination = _resolve(config, output)
    settings = config.get("common_gnn", {})
    if dry_run:
        _emit({"model": model, "view": view, "graphs": sum(bool(r.get(f'{view}_path')) for r in rows)})
        return
    registry = RunRegistry(
        destination, f"train-{model}-{view}-seed-{seed}", config, _project_root(config), resume
    )
    registry.write(
        "data.json",
        {
            "contract": "common-graph-v1",
            "view": view,
            "coverage": {
                split: sum(row["split"] == split and bool(row.get(f"{view}_path")) for row in rows)
                for split in ("train", "validation", "test")
            },
            "catalog": str(catalog),
        },
    )
    try:
        result = train_common_gnn(rows, view, model, registry.path, settings, seed)
    except Exception as exc:
        registry.fail(f"{type(exc).__name__}: {exc}")
        raise
    registry.complete([registry.path / "predictions.parquet", registry.path / "metrics.json"])
    _emit(result)


def _paper_gate(name: str, config_path: Path, output: Path, dry_run: bool) -> None:
    config = load_config(config_path)
    requested = config.get(name, {}).get("fidelity", "paper_faithful")
    report = {
        "model": name,
        "requested_fidelity": requested,
        "status": "deferred_by_user_order" if dry_run else "blocked_missing_asset",
        "message": "Paper branch is intentionally gated and cannot fall back under a faithful name.",
        "output": str(_resolve(config, output)),
    }
    _emit(report)
    if not dry_run:
        raise typer.Exit(code=3)


@app.command("train-gred")
def train_gred_command(config_path: Annotated[Path, typer.Option("--config")], output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/runs"), dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    _paper_gate("gred", config_path, output, dry_run)


@app.command("train-sigdroid")
def train_sigdroid_command(config_path: Annotated[Path, typer.Option("--config")], output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/runs"), dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    _paper_gate("sigdroid", config_path, output, dry_run)


@app.command("train-anakin")
def train_anakin_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/runs"),
    seed: Annotated[int, typer.Option("--seed")] = 13,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    settings = config.get("anakin", {})
    if settings.get("fidelity", "reimplementation") == "paper_faithful":
        _paper_gate("anakin", config_path, output, dry_run)
        return
    _, _, catalog = _data(config)
    rows = load_catalog(catalog)
    feature_dir = _resolve(config, settings.get("feature_dir", "model-artifacts/anakin-features"))
    if dry_run:
        _emit({"model": "anakin_reimplementation_v1", "rows": sum(bool(r.get('anakin_path')) for r in rows), "feature_dir": feature_dir})
        return
    if not (feature_dir / "fit.json").exists():
        train_rows = [row for row in rows if row["split"] == "train" and row.get("anakin_path")]
        fit_anakin_features(
            train_rows,
            feature_dir,
            top_k=int(settings.get("top_k", 140)),
            embedding_dim=int(settings.get("embedding_dim", 100)),
            window=int(settings.get("window", 5)),
            seed=seed,
            epochs=int(settings.get("embedding_epochs", 5)),
        )
    registry = RunRegistry(
        _resolve(config, output),
        f"train-anakin-reimplementation-seed-{seed}",
        config,
        _project_root(config),
        resume,
    )
    registry.write(
        "data.json",
        {
            "contract": "anakin-api-transition-v1",
            "fidelity": "reimplementation",
            "feature_dir": str(feature_dir),
            "catalog": str(catalog),
        },
    )
    try:
        result = train_anakin(rows, feature_dir, registry.path, settings, seed)
    except Exception as exc:
        registry.fail(f"{type(exc).__name__}: {exc}")
        raise
    registry.complete([registry.path / "predictions.parquet", registry.path / "metrics.json"])
    _emit(result)


@app.command("evaluate")
def evaluate_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    predictions: Annotated[Path, typer.Option("--predictions", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/evaluation"),
    split: Annotated[str, typer.Option("--split")] = "test",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    if dry_run:
        _emit({"predictions": predictions, "split": split, "output": _resolve(config, output)})
        return
    _emit(evaluate_predictions(predictions, _resolve(config, output), split))


@app.command("status")
def status_command(
    config_path: Annotated[Path, typer.Option("--config", exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output")] = Path("model-artifacts/runs"),
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    config = load_config(config_path)
    root = _resolve(config, output)
    runs = []
    if root.exists():
        for run_file in sorted(root.glob("*/run.json")):
            run = json.loads(run_file.read_text())
            runs.append({"run_id": run.get("run_id"), "status": run.get("status"), "path": str(run_file.parent)})
    _emit({"runs": runs, "dry_run": dry_run})


def main() -> None:
    app()


if __name__ == "__main__":
    main()
