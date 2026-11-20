from __future__ import annotations

from typing import Any

import torch
from torch import nn
from torch_geometric.nn import (
    GATConv,
    GCNConv,
    GINConv,
    GINEConv,
    global_max_pool,
    global_mean_pool,
)

from ..contracts import ContractError


class CommonGNN(nn.Module):
    """Representation-controlled GCN/GAT/GIN or explicitly edge-aware GINE."""

    def __init__(
        self,
        input_dim: int,
        kind: str = "gcn",
        hidden_dim: int = 64,
        layers: int = 2,
        dropout: float = 0.2,
        heads: int = 2,
        train_eps: bool = True,
        edge_dim: int | None = None,
        add_self_loops: bool = True,
    ) -> None:
        super().__init__()
        if kind not in {"gcn", "gat", "gin", "gine"}:
            raise ContractError(f"unsupported common GNN kind: {kind}")
        if layers < 1:
            raise ContractError("GNN must have at least one message-passing layer")
        if kind == "gine" and not edge_dim:
            raise ContractError("GINE requires edge_dim")
        self.kind = kind
        self.dropout = nn.Dropout(dropout)
        self.convs = nn.ModuleList()
        self.norms = nn.ModuleList()
        current = input_dim
        for _ in range(layers):
            if kind == "gcn":
                conv: nn.Module = GCNConv(
                    current, hidden_dim, normalize=True, add_self_loops=add_self_loops
                )
            elif kind == "gat":
                if hidden_dim % heads:
                    raise ContractError("GAT hidden_dim must be divisible by heads")
                conv = GATConv(
                    current,
                    hidden_dim // heads,
                    heads=heads,
                    concat=True,
                    dropout=dropout,
                    add_self_loops=add_self_loops,
                )
            else:
                mlp = nn.Sequential(
                    nn.Linear(current, hidden_dim),
                    nn.ReLU(),
                    nn.Linear(hidden_dim, hidden_dim),
                )
                conv = (
                    GINEConv(mlp, train_eps=train_eps, edge_dim=edge_dim)
                    if kind == "gine"
                    else GINConv(mlp, train_eps=train_eps)
                )
            self.convs.append(conv)
            self.norms.append(nn.LayerNorm(hidden_dim))
            current = hidden_dim
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        batch: torch.Tensor | None = None,
        edge_attr: torch.Tensor | None = None,
    ) -> torch.Tensor:
        if batch is None:
            batch = torch.zeros(x.shape[0], dtype=torch.long, device=x.device)
        if self.kind == "gin" and edge_attr is not None:
            raise ContractError("standard GIN does not accept edge_attr; use GINE")
        for conv, norm in zip(self.convs, self.norms, strict=True):
            if self.kind == "gine":
                if edge_attr is None:
                    raise ContractError("GINE requires edge_attr")
                x = conv(x, edge_index, edge_attr)
            else:
                x = conv(x, edge_index)
            x = self.dropout(torch.relu(norm(x)))
        pooled = torch.cat((global_mean_pool(x, batch), global_max_pool(x, batch)), dim=-1)
        return self.classifier(pooled).squeeze(-1)

    def architecture(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "readout": "global_mean_plus_global_max",
            "output": "single_logit",
            "edge_use": self.kind == "gine",
            "directedness": "preserved",
        }
