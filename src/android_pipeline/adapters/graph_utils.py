from __future__ import annotations

from ..ir.ids import stable_id


def owner_from_signature(signature: str) -> str:
    """Return the declaring class descriptor from a DEX-style signature."""
    owner, _, _ = signature.partition("->")
    return owner or "L<unknown>;"


def collect_call_graph(tables: dict[str, list[dict]]) -> dict:
    """Normalize canonical callsites into a provenance-preserving graph.

    The extractor keeps raw callsites and all resolved targets.  Views should
    derive from that IR instead of rebuilding edges from a collapsed graph.
    """
    methods = {row["method_id"]: row for row in tables.get("methods", [])}
    target_rows = tables.get("call_targets", [])
    callsites = {row["call_id"]: row for row in tables.get("callsites", [])}
    external_signatures = {
        row["target_method_id"]: row["target_signature"]
        for row in target_rows
        if row.get("target_kind") == "external"
    }
    method_to_class = {method_id: row["class_id"] for method_id, row in methods.items()}
    edges: dict[tuple[str, str], dict] = {}
    for target in target_rows:
        call = callsites.get(target.get("call_id"))
        if call is None:
            continue
        src = call.get("caller_method_id")
        dst = target.get("target_method_id")
        if not src or not dst:
            continue
        key = (src, dst)
        edge = edges.setdefault(
            key,
            {
                "src_method_id": src,
                "dst_method_id": dst,
                "target_kind": target.get("target_kind"),
                "target_signature": target.get("target_signature"),
                "raw_call_ids": [],
                "invoke_kinds": set(),
            },
        )
        edge["raw_call_ids"].append(call["call_id"])
        edge["invoke_kinds"].add(call.get("invoke_kind", "unknown"))

    node_ids = set(methods) | set(external_signatures)
    return {
        "methods": methods,
        "callsites": callsites,
        "external_signatures": external_signatures,
        "method_to_class": method_to_class,
        "edges": [
            {
                **edge,
                "raw_call_ids": sorted(set(edge["raw_call_ids"])),
                "invoke_kinds": sorted(edge["invoke_kinds"]),
            }
            for edge in sorted(
                edges.values(), key=lambda row: (row["src_method_id"], row["dst_method_id"])
            )
        ],
        "node_ids": sorted(node_ids),
    }


def external_class_id(signature: str) -> str:
    """Use the declaring owner only so external methods collapse to one class."""
    return stable_id("external-class", owner_from_signature(signature))


def class_graph(tables: dict[str, list[dict]]) -> dict:
    """Collapse method-level call evidence into class-level edges."""
    graph = collect_call_graph(tables)
    class_rows = {row["class_id"]: row for row in tables.get("classes", [])}
    method_to_class = graph["method_to_class"]
    external_classes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    for edge in graph["edges"]:
        src = method_to_class.get(edge["src_method_id"])
        if src is None:
            continue
        dst_method = edge["dst_method_id"]
        if dst_method in method_to_class:
            dst = method_to_class[dst_method]
        else:
            signature = graph["external_signatures"].get(dst_method, edge["target_signature"] or "")
            dst = external_class_id(signature)
            external_classes.setdefault(
                dst,
                {
                    "class_id": dst,
                    "descriptor": owner_from_signature(signature),
                    "is_defined": False,
                },
            )
        key = (src, dst)
        row = edges.setdefault(
            key,
            {"src": src, "dst": dst, "raw_call_ids": [], "method_edges": []},
        )
        row["raw_call_ids"].extend(edge["raw_call_ids"])
        row["method_edges"].append([edge["src_method_id"], edge["dst_method_id"]])
    nodes = {}
    for class_id, row in class_rows.items():
        nodes[class_id] = {
            "class_id": class_id,
            "descriptor": row.get("descriptor"),
            "is_defined": bool(row.get("is_defined", True)),
        }
    nodes.update(external_classes)
    return {
        "nodes": nodes,
        "edges": [
            {
                **row,
                "raw_call_ids": sorted(set(row["raw_call_ids"])),
                "method_edges": sorted(row["method_edges"]),
            }
            for row in sorted(edges.values(), key=lambda item: (item["src"], item["dst"]))
        ],
        "method_graph": graph,
    }


def security_concepts(signature: str, taxonomy: dict) -> list[str]:
    return sorted(
        concept
        for concept, patterns in taxonomy.get("concepts", {}).items()
        if any(pattern in signature for pattern in patterns)
    )
