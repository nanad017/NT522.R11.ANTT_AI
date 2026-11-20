"""Graph Convolutional Network architecture."""

from android_models.models.common_gnn import CommonGNN


class GCN(CommonGNN):
    def __init__(self, input_dim: int, **kwargs):
        super().__init__(input_dim=input_dim, kind="gcn", **kwargs)


__all__ = ["GCN"]
