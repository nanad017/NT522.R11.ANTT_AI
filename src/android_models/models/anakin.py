from __future__ import annotations

import torch
from torch import nn
from torch_geometric.nn import GCNConv, global_mean_pool

from ..contracts import ContractError


class AnakinGCN(nn.Module):
    def __init__(
        self,
        input_dim: int = 100,
        hidden_dim: int = 64,
        graph_layers: int = 2,
        fc_layers: int = 3,
        dropout: float = 0.19,
    ) -> None:
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        self.convs = nn.ModuleList()
        current = input_dim
        for _ in range(graph_layers):
            self.convs.append(GCNConv(current, hidden_dim, add_self_loops=True, normalize=True))
            current = hidden_dim
        layers: list[nn.Module] = []
        for _ in range(max(0, fc_layers - 1)):
            layers.extend((nn.Linear(current, hidden_dim), nn.ReLU(), nn.Dropout(dropout)))
            current = hidden_dim
        layers.append(nn.Linear(current, 1))
        self.classifier = nn.Sequential(*layers)

    def forward_graph(self, graph) -> torch.Tensor:
        x = graph.x
        for conv in self.convs:
            x = self.dropout(torch.relu(conv(x, graph.edge_index, edge_weight=graph.edge_weight)))
        batch = torch.zeros(x.shape[0], dtype=torch.long, device=x.device)
        return self.classifier(global_mean_pool(x, batch)).reshape(())

    def aggregate_apk(self, graphs: list) -> tuple[torch.Tensor, torch.Tensor]:
        if not graphs:
            raise ContractError("ANAKIN APK has no graph after pruning")
        logits = torch.stack([self.forward_graph(graph) for graph in graphs])
        probability = torch.sigmoid(logits).mean()
        return probability, logits

