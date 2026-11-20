from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.preprocessing import MaxAbsScaler

from android_models.features.classical import (
    fit_vectorizer,
    load_matrix,
    load_named_records,
    transform_records,
)
from android_models.models.classical import (
    create_classical,
    load_classical,
    model_scores,
    save_classical,
)


def test_train_only_sparse_vectorizer_and_order(synthetic_catalog, tmp_path: Path) -> None:
    _, rows = synthetic_catalog
    records = load_named_records(rows)
    train = [row for row in records if row["split"] == "train"]
    output = tmp_path / "features"
    metadata = fit_vectorizer(train, output)
    transformed = transform_records(records, output / "vectorizer.json", output)
    assert metadata["fit_rows"] == 2
    assert transformed["splits"]["test"]["shape"][1] == len(metadata["feature_names"])
    matrix, matrix_rows = load_matrix(output, "validation")
    assert sparse.isspmatrix_csr(matrix)
    assert matrix.dtype == np.float32
    assert [row["apk_sha256"] for row in matrix_rows] == sorted(row["apk_sha256"] for row in matrix_rows)


def test_safe_classical_checkpoint_roundtrip(tmp_path: Path) -> None:
    x = sparse.csr_matrix(np.asarray([[0, 1], [1, 0], [0, 2], [2, 0]], dtype=np.float32))
    y = np.asarray([0, 1, 0, 1])
    for kind, params in (
        ("linear_svm", {"C": 0.1, "max_iter": 1000}),
        ("random_forest", {"n_estimators": 5, "n_jobs": 1}),
        ("xgboost", {"n_estimators": 3, "early_stopping_rounds": None, "n_jobs": 1}),
    ):
        model = create_classical(kind, params, seed=13)
        if kind == "xgboost":
            model.set_params(early_stopping_rounds=None)
        model.fit(x, y)
        before, _ = model_scores(model, kind, x)
        directory = tmp_path / kind
        save_classical(model, kind, directory, {"test": True})
        loaded, _ = load_classical(directory)
        after, _ = model_scores(loaded, kind, x)
        np.testing.assert_allclose(after, before, rtol=1e-6, atol=1e-7)


def test_linear_svm_accepts_convergence_controls() -> None:
    model = create_classical(
        "linear_svm",
        {"C": 0.01, "dual": "auto", "tol": 1e-3, "max_iter": 100_000},
        seed=42,
    )

    assert model.named_steps["classifier"].C == 0.01
    assert model.named_steps["classifier"].dual == "auto"
    assert model.named_steps["classifier"].tol == 1e-3
    assert model.named_steps["classifier"].max_iter == 100_000
    assert isinstance(model.named_steps["scale"], MaxAbsScaler)


def test_linear_svm_scaling_is_sparse_and_fit_on_training_data_only() -> None:
    train = sparse.csr_matrix(
        np.asarray([[0.0, 1.0], [1000.0, 0.0], [500.0, 1.0], [0.0, 2.0]], dtype=np.float32)
    )
    labels = np.asarray([0, 1, 1, 0])
    model = create_classical("linear_svm", {"C": 0.01, "max_iter": 10_000}, seed=13)

    model.fit(train, labels)
    transformed = model.named_steps["scale"].transform(train)

    assert sparse.issparse(transformed)
    np.testing.assert_allclose(model.named_steps["scale"].max_abs_, [1000.0, 2.0])
