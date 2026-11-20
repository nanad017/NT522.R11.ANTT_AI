from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
import numpy as np

from ..ir.ids import stable_id
from ..ir.io import write_json_atomic

INVOKE_KINDS = [
    "direct",
    "static",
    "virtual",
    "interface",
    "super",
    "custom",
    "polymorphic",
    "unknown",
]


def build_fcg(
    tables: dict[str, list[dict]],
    output: str | Path,
    taxonomy: dict,
    mapping_mode: str = "full",
) -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    methods = {m["method_id"]: m for m in tables.get("methods", [])}
    target_rows = tables.get("call_targets", [])
    external = {
        r["target_method_id"]: r["target_signature"]
        for r in target_rows
        if r["target_kind"] == "external"
    }
    node_ids = sorted(set(methods) | set(external))
    node_index = {node: i for i, node in enumerate(node_ids)}
    ins_by_method = defaultdict(Counter)
    for row in tables.get("instructions", []):
        ins_by_method[row["method_id"]][int(row["opcode"]) & 0xFF] += 1
    out_calls, in_calls = Counter(), Counter()
    call_by_id = {r["call_id"]: r for r in tables.get("callsites", [])}
    edges: dict[tuple[str, str], dict] = {}
    for target in target_rows:
        call = call_by_id.get(target["call_id"])
        if not call:
            continue
        src, dst = call["caller_method_id"], target["target_method_id"]
        if src not in node_index or dst not in node_index:
            continue
        out_calls[src] += 1
        in_calls[dst] += 1
        key = (src, dst)
        edge = edges.setdefault(key, {"raw_call_ids": [], "invoke_kinds": Counter()})
        edge["raw_call_ids"].append(call["call_id"])
        kind = call["invoke_kind"] if call["invoke_kind"] in INVOKE_KINDS else "unknown"
        edge["invoke_kinds"][kind] += 1

    concepts = list(taxonomy["concepts"])
    feature_names = (
        [
            "node_type.internal",
            "node_type.external",
            "missing.code",
            "structure.log1p_instructions",
            "structure.log1p_in_calls",
            "structure.log1p_out_calls",
        ]
        + [f"security.{c}" for c in concepts]
        + [f"opcode.{i:02x}.count_log1p" for i in range(256)]
    )
    x = np.zeros((len(node_ids), len(feature_names)), dtype=np.float32)
    for node, i in node_index.items():
        internal = node in methods
        x[i, 0 if internal else 1] = 1
        x[i, 2] = 0 if internal and methods[node]["code_available"] else 1
        x[i, 3] = math.log1p(methods[node].get("instruction_count") or 0) if internal else 0
        x[i, 4] = math.log1p(in_calls[node])
        x[i, 5] = math.log1p(out_calls[node])
        signature = methods[node]["signature"] if internal else external[node]
        for j, concept in enumerate(concepts, start=6):
            if any(pattern in signature for pattern in taxonomy["concepts"][concept]):
                x[i, j] = 1
        base = 6 + len(concepts)
        for opcode, count in ins_by_method[node].items():
            x[i, base + opcode] = math.log1p(count)

    edge_rows = []
    for (src, dst), evidence in sorted(edges.items()):
        raw = sorted(evidence["raw_call_ids"])
        eid = stable_id("graph-edge", "fcg-v1", src, dst, "direct-call")
        edge_rows.append((src, dst, eid, raw, evidence["invoke_kinds"]))
    edge_index = (
        np.array(
            [[node_index[s] for s, *_ in edge_rows], [node_index[d] for _, d, *_ in edge_rows]],
            dtype=np.int64,
        )
        if edge_rows
        else np.empty((2, 0), dtype=np.int64)
    )
    edge_attr = np.zeros((len(edge_rows), 1 + len(INVOKE_KINDS)), dtype=np.float32)
    for i, row in enumerate(edge_rows):
        kinds = row[4]
        edge_attr[i, 0] = math.log1p(len(row[3]))
        for j, kind in enumerate(INVOKE_KINDS, start=1):
            edge_attr[i, j] = 1 if kinds[kind] else 0
    sample = tables["samples"][0]
    np.savez_compressed(
        output / "graph.npz",
        x=x,
        edge_index=edge_index,
        edge_attr=edge_attr,
        y=np.array([sample["label_binary"]], dtype=np.int64),
    )
    mapping = {
        "view_version": "fcg-v1",
        "mapping_mode": mapping_mode,
        "apk_sha256": sample["apk_sha256"],
        "node_count": len(node_ids),
        "edge_count": len(edge_rows),
        "feature_names": feature_names,
        "edge_feature_names": ["log1p_callsite_multiplicity"]
        + [f"invoke.{k}" for k in INVOKE_KINDS],
    }
    if mapping_mode == "full":
        mapping.update(
            {
                "node_ids": node_ids,
                "edge_ids": [r[2] for r in edge_rows],
                "edges": [
                    {"edge_id": r[2], "src": r[0], "dst": r[1], "raw_call_ids": r[3]}
                    for r in edge_rows
                ],
            }
        )
    elif mapping_mode != "model_only":
        raise ValueError(f"unsupported FCG mapping mode: {mapping_mode}")
    write_json_atomic(output / "mapping.json", mapping)
    return {"nodes": len(node_ids), "edges": len(edge_rows), "features": len(feature_names)}


def _quantiles(values: list[float], prefix: str, features: Counter) -> None:
    array = np.asarray(values or [0.0], dtype=np.float64)
    features[f"{prefix}.mean"] = float(array.mean())
    features[f"{prefix}.std"] = float(array.std())
    features[f"{prefix}.max"] = float(array.max())
    for quantile in (0.25, 0.5, 0.75, 0.95):
        features[f"{prefix}.q{int(quantile * 100):02d}"] = float(np.quantile(array, quantile))


def _signature_package(signature: str) -> str:
    owner = signature.split("->", 1)[0].lstrip("L")
    parts = owner.split("/")
    return ".".join(parts[: min(3, len(parts))]) if parts else "unknown"


def classical_record(tables: dict[str, list[dict]], taxonomy: dict | None = None) -> dict:
    """Produce behavior-only, named sparse features for all classical detectors.

    Identity, time, market, certificate, archive, path, VT, and label-derived
    fields are deliberately excluded from the feature mapping.
    """
    sample = tables["samples"][0]
    features = Counter()
    for p in tables.get("permissions", []):
        kind = p.get("permission_kind") or "unknown"
        features[f"manifest.permission.{kind}::{p['permission_name']}"] = 1.0
    for component in tables.get("components", []):
        component_type = component.get("component_type") or "unknown"
        features[f"manifest.component.type::{component_type}"] += 1.0
        for field in ("exported_raw", "enabled_raw"):
            value = str(component.get(field)).lower() if component.get(field) is not None else "missing"
            features[f"manifest.component.{field}::{value}"] += 1.0
    for intent in tables.get("intent_filters", []):
        for kind in ("actions", "categories"):
            for value in intent.get(kind) or []:
                features[f"manifest.intent.{kind}::{value}"] += 1.0
    for rule in tables.get("provider_rules", []):
        features["manifest.provider.has_authorities"] += float(bool(rule.get("authorities")))
        features["manifest.provider.has_read_permission"] += float(
            bool(rule.get("read_permission"))
        )
        features["manifest.provider.has_write_permission"] += float(
            bool(rule.get("write_permission"))
        )
        features["manifest.provider.grant_uri_permissions"] += float(
            str(rule.get("grant_uri_permissions")).lower() == "true"
        )
        try:
            path_rules = json.loads(rule.get("path_rules_json") or "[]")
        except json.JSONDecodeError:
            path_rules = []
        features["manifest.provider.path_rule_count"] += float(len(path_rules))
    for item in tables.get("app_config", []):
        kind = item.get("config_kind") or "unknown"
        features[f"manifest.app_config.kind::{kind}"] += 1.0
        features[f"manifest.app_config.required::{item.get('required_raw') or 'missing'}"] += 1.0

    instructions = tables.get("instructions", [])
    opcode_counts = Counter(int(ins["opcode"]) & 0xFF for ins in instructions)
    total_instructions = sum(opcode_counts.values())
    for opcode in range(256):
        features[f"opcode.bin.{opcode:02x}.normalized"] = (
            opcode_counts[opcode] / total_instructions if total_instructions else 0.0
        )
    for ins in instructions:
        features[f"opcode::{ins['opcode_name']}"] += 1.0
    features["coverage.instructions"] = float(total_instructions)
    features["coverage.missing_code_methods"] = float(
        sum(not row.get("code_available") for row in tables.get("methods", []))
    )
    features["coverage.instruction_decode_failure"] = float(
        sum(row.get("decode_status") != "succeeded" for row in instructions)
    )

    methods = {row["method_id"]: row for row in tables.get("methods", [])}
    calls = {row["call_id"]: row for row in tables.get("callsites", [])}
    targets = tables.get("call_targets", [])
    nodes = set(methods)
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    edge_multiplicity = Counter()
    external_signatures: dict[str, str] = {}
    sensitive_nodes: set[str] = set()
    for target in targets:
        call = calls.get(target["call_id"])
        if not call:
            continue
        src, dst = call["caller_method_id"], target["target_method_id"]
        nodes.update((src, dst))
        graph.add_edge(src, dst)
        edge_multiplicity[(src, dst)] += 1
        target_kind = target.get("target_kind") or "unknown"
        features[f"api.target_kind::{target_kind}"] += 1.0
        signature = target.get("target_signature") or ""
        if target_kind == "external":
            external_signatures[dst] = signature
            features[f"api.external.signature::{signature}"] += 1.0
            features[f"api.external.package::{_signature_package(signature)}"] += 1.0
        if taxonomy:
            for concept, patterns in taxonomy.get("concepts", {}).items():
                if any(pattern in signature for pattern in patterns):
                    features[f"security.concept::{concept}"] += 1.0
                    sensitive_nodes.update((src, dst))
    for call in calls.values():
        kind = call.get("invoke_kind") or "unknown"
        features[f"api.invoke_kind::{kind}"] += 1.0
        status = call.get("resolution_status") or "unknown"
        features[f"coverage.call_resolution::{status}"] += 1.0
    graph.add_nodes_from(nodes)
    node_count, edge_count = graph.number_of_nodes(), graph.number_of_edges()
    features["graph.fcg.nodes"] = float(node_count)
    features["graph.fcg.edges"] = float(edge_count)
    features["graph.fcg.density"] = float(nx.density(graph)) if node_count > 1 else 0.0
    features["graph.fcg.internal_node_ratio"] = len(methods) / node_count if node_count else 0.0
    features["graph.fcg.external_node_ratio"] = len(external_signatures) / node_count if node_count else 0.0
    _quantiles([float(value) for _, value in graph.in_degree()], "graph.fcg.in_degree", features)
    _quantiles([float(value) for _, value in graph.out_degree()], "graph.fcg.out_degree", features)
    strongly_connected = list(nx.strongly_connected_components(graph)) if node_count else []
    weakly_connected = list(nx.weakly_connected_components(graph)) if node_count else []
    features["graph.fcg.scc_count"] = float(len(strongly_connected))
    features["graph.fcg.largest_scc_ratio"] = (
        max(map(len, strongly_connected), default=0) / node_count if node_count else 0.0
    )
    features["graph.fcg.weak_component_count"] = float(len(weakly_connected))
    features["graph.fcg.isolates"] = float(len(list(nx.isolates(graph))))
    features["graph.fcg.self_loops"] = float(nx.number_of_selfloops(graph))
    non_loop_edges = {(src, dst) for src, dst in graph.edges if src != dst}
    reciprocal = sum((dst, src) in non_loop_edges for src, dst in non_loop_edges)
    features["graph.fcg.reciprocal_edge_ratio"] = (
        reciprocal / len(non_loop_edges) if non_loop_edges else 0.0
    )
    features["graph.fcg.sensitive_nodes"] = float(len(sensitive_nodes))
    features["graph.fcg.sensitive_edges"] = float(
        sum(src in sensitive_nodes or dst in sensitive_nodes for src, dst in graph.edges)
    )
    _quantiles(
        [float(value) for value in edge_multiplicity.values()],
        "graph.fcg.callsite_multiplicity",
        features,
    )
    return {
        "apk_sha256": sample["apk_sha256"],
        "label": sample["label_binary"],
        "split": sample["split"],
        "year": sample["year"],
        "features": dict(sorted(features.items())),
    }
