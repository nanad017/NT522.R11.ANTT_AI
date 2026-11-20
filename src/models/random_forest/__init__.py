"""Random Forest factory."""

from android_models.models.classical import create_classical


def build_model(params: dict, seed: int = 13):
    return create_classical("random_forest", params, seed)


__all__ = ["build_model"]
