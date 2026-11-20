"""Extraction pipeline compatibility facade.

The executable implementation remains in :mod:`android_pipeline` so historical
commands and artifact contracts stay reproducible.
"""

from android_pipeline.config import PipelineConfig, load_config
from android_pipeline.worker import extract_to_directory

__all__ = ["PipelineConfig", "extract_to_directory", "load_config"]
