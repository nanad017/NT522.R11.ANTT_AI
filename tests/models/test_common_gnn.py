from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from android_models.contracts import ContractError
from android_models.features.graph import DynamicGraphBatchSampler, load_graph
from android_models.models.common_gnn import CommonGNN
from android_models.quality import graph_quality
from android_models.training.gnn import load_trained_gnn, train_common_gnn


def test_graph_contract_and_common_models(synthetic_catalog) -> None:
    _, rows = synthetic_catalog
    graph = load_graph(rows[0], "fcg")
    batch = torch.zeros(graph.num_nodes, dtype=torch.long)
    for kind in ("gcn", "gat", "gin"):
        torch.manual_seed(13)
        model = CommonGNN(input_dim=3, kind=kind, hidden_dim=8, layers=2, heads=2)
        logits = model(graph.x, graph.edge_index, batch)
        assert logits.shape == (1,)
        assert torch.isfinite(logits).all()
        logits.sum().backward()
        assert all(parameter.grad is not None for parameter in model.parameters())
    gin = CommonGNN(input_dim=3, kind="gin", hidden_dim=8)
    with pytest.raises(ContractError):
        gin(graph.x, graph.edge_index, batch, graph.edge_attr)
    gine = CommonGNN(input_dim=3, kind="gine", hidden_dim=8, edge_dim=2)
    assert gine(graph.x, graph.edge_index, batch, graph.edge_attr).shape == (1,)
    quality = graph_quality(rows, "fcg")
    assert quality["available"] == 6
    assert quality["zero_edge"] == 0
    assert quality["non_finite_feature_arrays"] == 0


def test_compact_model_only_mapping_contract(synthetic_catalog) -> None:
    _, rows = synthetic_catalog
    path = rows[0]["fcg_path"]
    mapping_path = Path(path).parent / "mapping.json"
    mapping = json.loads(mapping_path.read_text())
    mapping.pop("node_ids")
    mapping.pop("edge_ids")
    mapping["mapping_mode"] = "model_only"
    mapping["node_count"] = 2
    mapping["edge_count"] = 1
    mapping_path.write_text(json.dumps(mapping))
    graph = load_graph(rows[0], "fcg")
    assert graph.mapping_mode == "model_only"
    assert graph.num_nodes == 2


def test_dynamic_budget_keeps_oversized_graph() -> None:
    sampler = DynamicGraphBatchSampler([(3, 2), (100, 200), (2, 1)], 10, 10)
    batches = list(sampler)
    assert batches == [[0], [1], [2]]
    assert sorted(index for batch in batches for index in batch) == [0, 1, 2]


def test_directed_orientation_source_to_target() -> None:
    from torch_geometric.nn import GCNConv

    conv = GCNConv(1, 1, add_self_loops=False, normalize=False, bias=False)
    with torch.no_grad():
        conv.lin.weight.fill_(1.0)
    x = torch.tensor([[2.0], [0.0]])
    output = conv(x, torch.tensor([[0], [1]]))
    assert output[0].item() == 0.0
    assert output[1].item() == 2.0


def test_tiny_gnn_train_checkpoint_and_apk_predictions(synthetic_catalog, tmp_path) -> None:
    _, rows = synthetic_catalog
    result = train_common_gnn(
        rows,
        "fcg",
        "gcn",
        tmp_path / "run",
        {
            "hidden_dim": 8,
            "layers": 1,
            "epochs": 2,
            "patience": 2,
            "max_nodes_per_batch": 20,
            "max_edges_per_batch": 20,
            "num_workers": 0,
        },
        seed=13,
    )
    assert result["status"] == "complete"
    model, metadata = load_trained_gnn(tmp_path / "run")
    assert model.training is False
    assert metadata["view"] == "fcg"
    assert (tmp_path / "run" / "predictions.parquet").exists()
