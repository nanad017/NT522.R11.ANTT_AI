from __future__ import annotations

import torch
from torch import nn
from torch_geometric.nn import GCNConv

from ..contracts import ContractError


class GREDApprox(nn.Module):
    """GRED-shaped region pooling over project features, never paper-faithful."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        layers: int = 2,
        top_k_regions: int = 5,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.top_k_regions = top_k_regions
        self.dropout = nn.Dropout(dropout)
        self.convs = nn.ModuleList()
        current = input_dim
        for _ in range(layers):
            self.convs.append(GCNConv(current, hidden_dim, add_self_loops=True))
            current = hidden_dim
        self.region_score = nn.Linear(hidden_dim * 2, 1)
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim * 4, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, graph, regions: list[torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        x = graph.x
        for conv in self.convs:
            x = self.dropout(torch.relu(conv(x, graph.edge_index)))
        if not regions:
            regions = [torch.arange(x.shape[0], device=x.device)]
        representations = []
        for membership in regions:
            membership = membership.to(x.device)
            if membership.numel() == 0:
                continue
            values = x[membership]
            representations.append(torch.cat((values.max(dim=0).values, values.mean(dim=0))))
        if not representations:
            raise ContractError("GRED graph has no nonempty sensitive region")
        region_matrix = torch.stack(representations)
        scores = self.region_score(region_matrix).squeeze(-1)
        count = min(self.top_k_regions, scores.numel())
        selected = torch.topk(scores, count).indices
        important = region_matrix[selected]
        graph_embedding = torch.cat((important.max(dim=0).values, important.mean(dim=0)))
        logit = self.classifier(graph_embedding).reshape(())
        return logit, scores, selected

