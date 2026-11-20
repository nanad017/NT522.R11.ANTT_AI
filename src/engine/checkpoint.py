"""Safe checkpoint loading APIs."""

from android_models.models.classical import load_classical
from android_models.training.gnn import load_trained_gnn

__all__ = ["load_classical", "load_trained_gnn"]
