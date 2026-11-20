"""Training entry points."""

from android_models.training.classical import train_classical
from android_models.training.gnn import train_common_gnn

__all__ = ["train_classical", "train_common_gnn"]
