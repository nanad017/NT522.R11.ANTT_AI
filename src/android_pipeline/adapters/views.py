from __future__ import annotations

from pathlib import Path

from ..config import PipelineConfig
from .anakin import build_anakin
from .ccg import build_ccg
from .fcg import build_fcg
from .gred import build_gred
from .sigdroid import build_sigdroid


def build_views(
    tables: dict[str, list[dict]],
    output: str | Path,
    cfg: PipelineConfig,
    taxonomy: dict,
) -> dict[str, dict]:
    """Build the configured model views from one canonical IR result."""
    output = Path(output)
    requested = set(cfg.graphs.views)
    results: dict[str, dict] = {}
    if "fcg" in requested:
        results["fcg"] = build_fcg(
            tables,
            output / "fcg" / tables["samples"][0]["apk_sha256"],
            taxonomy,
            cfg.artifacts.fcg_mapping,
        )
    if "ccg" in requested:
        results["ccg"] = build_ccg(
            tables, output / "ccg" / tables["samples"][0]["apk_sha256"], taxonomy
        )
    if "gred" in requested:
        results["gred"] = build_gred(
            tables,
            output / "gred" / tables["samples"][0]["apk_sha256"],
            taxonomy,
            cfg.paper_adapters.get("gred", {}),
        )
    if "sigdroid" in requested:
        results["sigdroid"] = build_sigdroid(
            tables,
            output / "sigdroid" / tables["samples"][0]["apk_sha256"],
            taxonomy,
            cfg.paper_adapters.get("sigdroid", {}),
        )
    if "anakin" in requested:
        results["anakin"] = build_anakin(
            tables, output / "anakin" / tables["samples"][0]["apk_sha256"]
        )
    unknown = requested - {"fcg", "ccg", "gred", "sigdroid", "anakin"}
    if unknown:
        raise ValueError(f"unsupported graph views: {sorted(unknown)}")
    return results
