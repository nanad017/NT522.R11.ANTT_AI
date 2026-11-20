"""Graph Isomorphism Network architecture."""

from android_models.models.common_gnn import CommonGNN


class GIN(CommonGNN):
    def __init__(self, input_dim: int, **kwargs):
        super().__init__(input_dim=input_dim, kind="gin", **kwargs)


__all__ = ["GIN"]
