"""Graph Attention Network architecture."""

from android_models.models.common_gnn import CommonGNN


class GAT(CommonGNN):
    def __init__(self, input_dim: int, **kwargs):
        super().__init__(input_dim=input_dim, kind="gat", **kwargs)


__all__ = ["GAT"]
