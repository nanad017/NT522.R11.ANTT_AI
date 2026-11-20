from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from androguard import __version__ as androguard_version
from androguard.core.apk import APK
from androguard.core.dex import DEX, InvalidInstruction
from androguard.misc import AnalyzeAPK
from cryptography import x509
from loguru import logger
from lxml import etree

from ..config import PipelineConfig
from ..ir.ids import (
    call_id,
    class_id,
    external_method_id,
    instruction_id,
    method_id,
    stable_id,
    utf8_text,
)

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
# Match the same official root-level multidex names accepted by Androguard.
# Some real APKs use classes0.dex/classes1.dex even though Android's usual
# convention starts secondary files at classes2.dex.
DEX_RE = re.compile(r"^classes(?:[0-9]+)?\.dex$")
logger.disable("androguard")

LIFECYCLE_METHODS = {
    "activity": {"onCreate", "onStart", "onResume", "onNewIntent"},
    "activity-alias": {"onCreate", "onStart", "onResume", "onNewIntent"},
    "service": {"onCreate", "onStartCommand", "onBind", "onHandleIntent"},
    "receiver": {"onReceive"},
    "provider": {"onCreate", "query", "insert", "update", "delete"},
}


def _apksigner_path() -> Path | None:
    system = shutil.which("apksigner")
    if system:
        return Path(system)
    local = Path(__file__).resolve().parents[3] / "scripts/apksigner-local"
    return local if local.is_file() else None


def _verify_signing(path: Path, apk_sha256: str, enabled: bool) -> dict:
    if not enabled:
        return {
            "apk_sha256": apk_sha256,
            "status": "not_enabled",
            "tool_name": "apksigner",
            "tool_version": None,
            "exit_code": None,
            "schemes_json": None,
            "signer_count": None,
            "stdout": None,
            "stderr": None,
        }
    tool = _apksigner_path()
    if tool is None:
        return {
            "apk_sha256": apk_sha256,
            "status": "tool_missing",
            "tool_name": "apksigner",
            "tool_version": None,
            "exit_code": None,
            "schemes_json": None,
            "signer_count": None,
            "stdout": None,
            "stderr": "apksigner was enabled but no executable was found",
        }
    version = subprocess.run(
        [str(tool), "version"], text=True, capture_output=True, timeout=30, check=False
    ).stdout.strip()
    proc = subprocess.run(
        [str(tool), "verify", "--verbose", "--print-certs", str(path)],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    schemes = {}
    for line in proc.stdout.splitlines():
        match = re.match(r"Verified using (v\d+) scheme.*:\s*(true|false)$", line)
        if match:
            schemes[match.group(1)] = match.group(2) == "true"
    signer_ids = {
        match.group(1)
        for line in proc.stdout.splitlines()
        if (match := re.match(r"Signer #(\d+) certificate", line))
    }
    return {
        "apk_sha256": apk_sha256,
        "status": "verified" if proc.returncode == 0 else "verification_failed",
        "tool_name": "apksigner",
        "tool_version": version or None,
        "exit_code": proc.returncode,
        "schemes_json": json.dumps(schemes, sort_keys=True),
        "signer_count": len(signer_ids),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "name"):
        return {"enum": value.name, "value": getattr(value, "value", None)}
    return str(value)


def _signature(method: Any) -> str:
    return f"{method.get_class_name()}->{method.get_name()}{method.get_descriptor()}"


def _declared_signature(output: str | None) -> str | None:
    """Recover an invoke target signature from Androguard's text rendering."""
    if not output:
        return None
    match = re.search(r"(L[^,\s]+;->[^,\s]+)", output)
    return match.group(1) if match else None


def _normalize_component(package: str, value: str) -> str:
    if value.startswith("."):
        return package + value
    if "." not in value:
        return package + "." + value
    return value


def _traverse_sequence(
    entry_mid: str,
    calls_by_caller: dict[str, list[dict]],
    targets_by_call: dict[str, list[dict]],
    policy: dict,
) -> tuple[list[str], list[dict], bool]:
    tokens: list[str] = []
    provenance: list[dict] = []
    truncated = False

    def walk(mid: str, depth: int, path: frozenset[str]) -> None:
        nonlocal truncated
        if truncated or depth > policy["max_depth"] or mid in path:
            return
        next_path = path | {mid}
        for call in calls_by_caller.get(mid, []):
            for target in sorted(
                targets_by_call.get(call["call_id"], []),
                key=lambda row: (row["target_kind"], row["target_signature"]),
            ):
                if target["target_kind"] == "external" and target["target_signature"].startswith(
                    tuple(policy["external_prefixes"])
                ):
                    tokens.append(target["target_signature"])
                    provenance.append(
                        {
                            "position": len(tokens) - 1,
                            "call_id": call["call_id"],
                            "caller_method_id": mid,
                            "offset_bytes": call["offset_bytes"],
                            "target_method_id": target["target_method_id"],
                        }
                    )
                    if len(tokens) >= policy["max_tokens"]:
                        truncated = True
                        return
                elif target["target_kind"] == "internal":
                    walk(target["target_method_id"], depth + 1, next_path)

    walk(entry_mid, 0, frozenset())
    return tokens, provenance, truncated


def validate_zip(
    path: Path, max_entries: int = 100_000, max_uncompressed: int = 2 << 30
) -> tuple[list[zipfile.ZipInfo], dict]:
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise ValueError(f"ZIP entry limit exceeded: {len(infos)}")
        seen: set[str] = set()
        unsafe_entries: list[str] = []
        total = 0
        for info in infos:
            p = PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts or "\x00" in info.filename:
                # APK members are never extracted to the filesystem. Preserve
                # the odd entry as inventory evidence instead of dropping the
                # whole APK; outer source archives remain strictly protected by
                # archive_reader.safe_member_name.
                unsafe_entries.append(info.filename)
            if info.filename in seen:
                raise ValueError(f"duplicate ZIP path: {info.filename!r}")
            seen.add(info.filename)
            total += info.file_size
            if total > max_uncompressed:
                raise ValueError("ZIP uncompressed-size limit exceeded")
        names = {x.filename for x in infos}
        required = ["AndroidManifest.xml", *(n for n in names if DEX_RE.fullmatch(n))]
        required_crc_failures = []
        for name in required:
            try:
                zf.read(name)
            except (KeyError, zipfile.BadZipFile) as exc:
                required_crc_failures.append(f"{name}: {exc}")
        if required_crc_failures:
            raise ValueError(f"required ZIP entry CRC/read failure: {required_crc_failures}")
        return infos, {
            "entry_count": len(infos),
            "uncompressed_bytes": total,
            "has_manifest": "AndroidManifest.xml" in names,
            "dex_entries": sorted(n for n in names if DEX_RE.fullmatch(n)),
            "unsafe_entry_names": sorted(unsafe_entries),
            "crc_scope": "required_manifest_and_dex",
        }


def extract_apk(
    apk_path: str | Path,
    manifest_row: dict[str, str],
    cfg: PipelineConfig,
    *,
    input_archive: str | None = None,
    input_member: str | None = None,
) -> tuple[dict[str, list[dict]], dict]:
    path = Path(apk_path)
    data_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = manifest_row["sha256"].lower()
    if data_hash != expected:
        raise ValueError(f"APK SHA-256 mismatch expected={expected} actual={data_hash}")
    infos, zip_diag = validate_zip(path)
    if not zip_diag["has_manifest"] or not zip_diag["dex_entries"]:
        raise ValueError("APK lacks AndroidManifest.xml or classes*.dex")

    decode_warnings: list[dict[str, str]] = []
    analysis_fallback = False
    try:
        a, dexes, analysis = AnalyzeAPK(str(path))
        dex_names = list(a.get_dex_names())
        if len(dex_names) != len(dexes):
            raise RuntimeError(
                f"Androguard DEX name/object mismatch: names={len(dex_names)} objects={len(dexes)}"
            )
        parsed_dexes = list(zip(dex_names, dexes, strict=True))
    except InvalidInstruction as exc:
        # Anti-analysis or malformed instructions can make Analysis.add abort
        # the entire APK. DEX metadata itself is often still parseable, so use
        # a declared-invoke fallback and isolate decode failures per method.
        analysis_fallback = True
        decode_warnings.append({"scope": "analysis", "reason": f"{type(exc).__name__}: {exc}"})
        a = APK(str(path))
        analysis = None
        parsed_dexes = []
        for name in a.get_dex_names():
            try:
                parsed_dexes.append(
                    (name, DEX(a.get_file(name), using_api=a.get_target_sdk_version()))
                )
            except Exception as dex_exc:  # noqa: BLE001 - retain other valid DEX files
                decode_warnings.append(
                    {"scope": name, "reason": f"{type(dex_exc).__name__}: {dex_exc}"}
                )
    if not parsed_dexes:
        raise ValueError("no parseable DEX files")
    tables: dict[str, list[dict]] = defaultdict(list)
    label = manifest_row["label"]
    sample = {
        "apk_sha256": expected,
        "label_binary": cfg.dataset.label_encoding[label],
        "label_text": label,
        "split": manifest_row["split"],
        "year": int(manifest_row["year"]),
        "apk_size": path.stat().st_size,
        "manifest_row_ref": manifest_row.get("manifest_row_number"),
        "analysis_status": "succeeded",
        "package_name": a.get_package() or None,
        "version_code": a.get_androidversion_code() or None,
        "version_name": a.get_androidversion_name() or None,
        "min_sdk": a.get_min_sdk_version() or None,
        "target_sdk": a.get_target_sdk_version() or None,
        "signed_v1": bool(a.is_signed_v1()),
        "signed_v2": bool(a.is_signed_v2()),
        "signed_v3": bool(a.is_signed_v3()),
        "analyzer_version": str(androguard_version),
        "config_hash": cfg.digest(),
        "input_archive": input_archive,
        "input_member": input_member,
        "error_reason": None,
    }
    tables["samples"].append(sample)

    certs: set[bytes] = set()
    for getter, scheme in [
        ("get_certificates_der_v1", "v1"),
        ("get_certificates_der_v2", "v2"),
        ("get_certificates_der_v3", "v3"),
    ]:
        if hasattr(a, getter):
            for der in getattr(a, getter)() or []:
                if der in certs:
                    continue
                certs.add(der)
                try:
                    certificate = x509.load_der_x509_certificate(der)
                    subject = certificate.subject.rfc4514_string()
                    issuer = certificate.issuer.rfc4514_string()
                    serial_hex = format(certificate.serial_number, "x")
                    not_valid_before = certificate.not_valid_before_utc.isoformat()
                    not_valid_after = certificate.not_valid_after_utc.isoformat()
                except Exception:  # noqa: BLE001 - preserve inventory for malformed certs
                    subject = issuer = serial_hex = not_valid_before = not_valid_after = None
                tables["certificates"].append(
                    {
                        "apk_sha256": expected,
                        "certificate_sha256": hashlib.sha256(der).hexdigest(),
                        "scheme_source": scheme,
                        "certificate_der_size": len(der),
                        "subject": subject,
                        "issuer": issuer,
                        "serial_hex": serial_hex,
                        "not_valid_before": not_valid_before,
                        "not_valid_after": not_valid_after,
                    }
                )

    signing = _verify_signing(path, expected, cfg.extraction.signing_verify)
    tables["signing_verification"].append(signing)

    for name in sorted(set(a.get_permissions() or [])):
        tables["permissions"].append(
            {
                "apk_sha256": expected,
                "permission_name": name,
                "permission_kind": "uses-permission",
                "max_sdk": None,
                "source": "androguard-apk",
            }
        )

    root = a.get_android_manifest_xml()
    package = a.get_package() or ""
    if root is not None:
        for tag in ("activity", "activity-alias", "service", "receiver", "provider"):
            for elem in root.findall(f".//{tag}"):
                raw = elem.get(ANDROID_NS + "name")
                if not raw:
                    continue
                normalized = _normalize_component(package, raw)
                component_stable_id = stable_id("component", expected, tag, normalized)
                tables["components"].append(
                    {
                        "apk_sha256": expected,
                        "component_id": component_stable_id,
                        "component_type": tag,
                        "name_raw": raw,
                        "name_normalized": normalized,
                        "exported_raw": elem.get(ANDROID_NS + "exported"),
                        "enabled_raw": elem.get(ANDROID_NS + "enabled"),
                        "process": elem.get(ANDROID_NS + "process"),
                        "permission": elem.get(ANDROID_NS + "permission"),
                        "source": "manifest-xml",
                    }
                )
                for filter_index, intent_filter in enumerate(elem.findall("intent-filter")):
                    actions = sorted(
                        value
                        for child in intent_filter.findall("action")
                        if (value := child.get(ANDROID_NS + "name"))
                    )
                    categories = sorted(
                        value
                        for child in intent_filter.findall("category")
                        if (value := child.get(ANDROID_NS + "name"))
                    )
                    data_rows = []
                    for child in intent_filter.findall("data"):
                        data_rows.append(
                            {
                                key: child.get(ANDROID_NS + key)
                                for key in (
                                    "scheme",
                                    "host",
                                    "port",
                                    "path",
                                    "pathPrefix",
                                    "pathPattern",
                                    "mimeType",
                                )
                                if child.get(ANDROID_NS + key) is not None
                            }
                        )
                    tables["intent_filters"].append(
                        {
                            "apk_sha256": expected,
                            "filter_id": stable_id(
                                "intent-filter", component_stable_id, filter_index
                            ),
                            "component_id": component_stable_id,
                            "actions": actions,
                            "categories": categories,
                            "data_json": json.dumps(data_rows, sort_keys=True),
                            "priority": intent_filter.get(ANDROID_NS + "priority"),
                        }
                    )
                if tag == "provider":
                    path_rules = []
                    for child in elem:
                        if child.tag not in {"grant-uri-permission", "path-permission"}:
                            continue
                        path_rules.append(
                            {
                                "kind": child.tag,
                                "attributes": {
                                    etree.QName(key).localname: value
                                    for key, value in child.attrib.items()
                                },
                            }
                        )
                    tables["provider_rules"].append(
                        {
                            "apk_sha256": expected,
                            "component_id": component_stable_id,
                            "authorities": elem.get(ANDROID_NS + "authorities"),
                            "read_permission": elem.get(ANDROID_NS + "readPermission"),
                            "write_permission": elem.get(ANDROID_NS + "writePermission"),
                            "grant_uri_permissions": elem.get(ANDROID_NS + "grantUriPermissions"),
                            "path_rules_json": json.dumps(path_rules, sort_keys=True),
                        }
                    )

        application = root.find("application")
        if application is not None:
            for key in (
                "name",
                "debuggable",
                "testOnly",
                "allowBackup",
                "usesCleartextTraffic",
                "networkSecurityConfig",
                "fullBackupContent",
            ):
                tables["app_config"].append(
                    {
                        "apk_sha256": expected,
                        "config_kind": "application-attribute",
                        "name": key,
                        "value": application.get(ANDROID_NS + key),
                        "required_raw": None,
                        "source": "manifest-xml",
                    }
                )
        for tag in ("uses-feature", "uses-library"):
            for elem in root.findall(f".//{tag}"):
                tables["app_config"].append(
                    {
                        "apk_sha256": expected,
                        "config_kind": tag,
                        "name": elem.get(ANDROID_NS + "name"),
                        "value": None,
                        "required_raw": elem.get(ANDROID_NS + "required"),
                        "source": "manifest-xml",
                    }
                )

    with zipfile.ZipFile(path) as zf:
        dex_payloads = {name: zf.read(name) for name, _dex in parsed_dexes}
        for info in infos:
            kind = "file"
            if info.filename.startswith("res/") or info.filename in {
                "resources.arsc",
                "AndroidManifest.xml",
            }:
                kind = "resource"
            elif info.filename.startswith("assets/"):
                kind = "asset"
            tables["resources"].append(
                {
                    "apk_sha256": expected,
                    "entry_path": info.filename,
                    "size": info.file_size,
                    "compressed_size": info.compress_size,
                    "crc32": f"{info.CRC:08x}",
                    "kind": kind,
                }
            )
            if info.filename.startswith("lib/") and info.filename.endswith(".so"):
                try:
                    payload = zf.read(info.filename)
                except zipfile.BadZipFile as exc:
                    decode_warnings.append(
                        {
                            "scope": info.filename,
                            "reason": f"optional ZIP entry unreadable: {exc}",
                        }
                    )
                    continue
                parts = PurePosixPath(info.filename).parts
                tables["native_libraries"].append(
                    {
                        "apk_sha256": expected,
                        "entry_path": info.filename,
                        "abi": parts[1] if len(parts) > 2 else None,
                        "size": info.file_size,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                )

    method_ids: dict[Any, str] = {}
    method_sigs: dict[str, str] = {}
    dex_ids: dict[int, str] = {}
    for index, (name, dex) in enumerate(parsed_dexes, start=1):
        payload = dex_payloads[name]
        dex_sha = hashlib.sha256(payload).hexdigest()
        did = stable_id("dex", expected, name, dex_sha)
        dex_ids[id(dex)] = did
        tables["dex_files"].append(
            {
                "apk_sha256": expected,
                "dex_id": did,
                "entry_path": name,
                "dex_index": index,
                "dex_sha256": dex_sha,
                "size": len(payload),
                "parse_status": "succeeded",
                "error": None,
            }
        )
        for cls in dex.get_classes():
            owner = cls.get_name()
            cid = class_id(expected, did, owner)
            tables["classes"].append(
                {
                    "apk_sha256": expected,
                    "class_id": cid,
                    "dex_id": did,
                    "descriptor": owner,
                    "superclass": cls.get_superclassname(),
                    "interfaces": list(cls.get_interfaces() or []),
                    "access_flags": int(cls.get_access_flags()),
                    "is_defined": True,
                    "classification_source": "dex-definition",
                }
            )
            for field in cls.get_fields():
                tables["fields"].append(
                    {
                        "apk_sha256": expected,
                        "field_id": stable_id(
                            "field", expected, did, owner, field.get_name(), field.get_descriptor()
                        ),
                        "class_id": cid,
                        "dex_id": did,
                        "owner": owner,
                        "name": field.get_name(),
                        "field_type": field.get_descriptor(),
                        "access_flags": int(field.get_access_flags()),
                        "encoded_value_json": json.dumps(
                            _jsonable(field.get_init_value()), sort_keys=True
                        )
                        if hasattr(field, "get_init_value")
                        else None,
                    }
                )
            for method in cls.get_methods():
                proto = method.get_descriptor()
                mid = method_id(expected, did, owner, method.get_name(), proto)
                method_ids[method] = mid
                sig = _signature(method)
                method_sigs[sig] = mid

    instruction_lookup: dict[tuple[str, int], str] = {}
    for dex_name, dex in parsed_dexes:
        did = dex_ids[id(dex)]
        for cls in dex.get_classes():
            owner = cls.get_name()
            cid = class_id(expected, did, owner)
            for method in cls.get_methods():
                mid = method_ids[method]
                code = method.get_code()
                instruction_rows = []
                offset_to_iid: dict[int, str] = {}
                calls_by_offset: dict[int, dict] = {}
                raw_code = b""
                if code is not None:
                    try:
                        raw_code = code.get_bc().get_raw()
                    except Exception:  # noqa: BLE001 - optional raw-code accessor varies by DEX
                        raw_code = b""
                try:
                    decoded_instructions = list(method.get_instructions_idx())
                except Exception as exc:  # noqa: BLE001 - isolate malformed/anti-analysis method
                    decoded_instructions = []
                    decode_warnings.append(
                        {
                            "scope": f"{dex_name}:{_signature(method)}",
                            "reason": f"{type(exc).__name__}: {exc}",
                        }
                    )
                for ordinal, (offset, ins) in enumerate(decoded_instructions):
                    iid = instruction_id(mid, offset)
                    instruction_lookup[(mid, offset)] = iid
                    offset_to_iid[offset] = iid
                    try:
                        raw_operands = ins.get_operands(offset)
                        operands = json.dumps(_jsonable(raw_operands), sort_keys=True)
                        output = ins.get_output(offset)
                        status = "succeeded"
                    except Exception as exc:  # noqa: BLE001 - preserve decode failure per instruction
                        raw_operands = []
                        operands, output, status = "[]", None, f"failed:{type(exc).__name__}"
                    row = {
                        "apk_sha256": expected,
                        "instruction_id": iid,
                        "method_id": mid,
                        "ordinal": ordinal,
                        "offset_bytes": offset,
                        "offset_unit": "byte",
                        "opcode": int(ins.get_op_value()),
                        "opcode_name": ins.get_name(),
                        "length_bytes": int(ins.get_length()),
                        "operands_json": operands,
                        "output_text": output,
                        "decode_status": status,
                    }
                    instruction_rows.append(row)
                    if cfg.extraction.strings and ins.get_name().startswith("const-string"):
                        values = [
                            operand[-1]
                            for operand in raw_operands
                            if isinstance(operand, (tuple, list))
                            and operand
                            and isinstance(operand[-1], str)
                        ]
                        if values:
                            value = utf8_text(values[-1])
                            tables["string_constants"].append(
                                {
                                    "apk_sha256": expected,
                                    "string_id": stable_id("string-use", iid, value),
                                    "method_id": mid,
                                    "instruction_id": iid,
                                    "offset_bytes": offset,
                                    "value": value,
                                    "value_sha256": hashlib.sha256(value.encode()).hexdigest(),
                                    "source": "dex-const-string",
                                }
                            )
                    if cfg.extraction.raw_invokes and ins.get_name().startswith("invoke-"):
                        kind = ins.get_name().removeprefix("invoke-").removesuffix("/range")
                        cid_call = call_id(iid, kind)
                        call_row = {
                            "apk_sha256": expected,
                            "call_id": cid_call,
                            "caller_method_id": mid,
                            "instruction_id": iid,
                            "offset_bytes": offset,
                            "invoke_kind": kind,
                            "declared_target_ref": output,
                            "resolution_status": "raw_declared",
                            "dex_id": did,
                        }
                        tables["callsites"].append(call_row)
                        calls_by_offset[offset] = call_row
                        if analysis_fallback:
                            signature = _declared_signature(output)
                            if signature:
                                target_id = method_sigs.get(signature) or external_method_id(
                                    signature
                                )
                                call_row["resolution_status"] = (
                                    "declared_internal"
                                    if signature in method_sigs
                                    else "declared_external"
                                )
                                tables["call_targets"].append(
                                    {
                                        "apk_sha256": expected,
                                        "call_id": cid_call,
                                        "target_method_id": target_id,
                                        "target_signature": signature,
                                        "target_kind": (
                                            "internal" if signature in method_sigs else "external"
                                        ),
                                        "evidence_source": "dex-declared-invoke",
                                        "resolution_policy": "declared-signature-v1",
                                    }
                                )
                if cfg.extraction.instructions:
                    tables["instructions"].extend(instruction_rows)
                tables["methods"].append(
                    {
                        "apk_sha256": expected,
                        "method_id": mid,
                        "class_id": cid,
                        "dex_id": did,
                        "owner": owner,
                        "name": method.get_name(),
                        "prototype": method.get_descriptor(),
                        "signature": _signature(method),
                        "access_flags": int(method.get_access_flags()),
                        "is_internal": True,
                        "code_available": code is not None,
                        "code_hash": hashlib.sha256(raw_code).hexdigest() if raw_code else None,
                        "register_count": code.get_registers_size() if code else None,
                        "code_size_bytes": code.get_length() if code else None,
                        "instruction_count": len(instruction_rows) if code else None,
                    }
                )

                ma = analysis.get_method(method) if analysis is not None else None
                if ma is None:
                    continue
                for _target_class, target_ma, offset in ma.get_xref_to():
                    target = target_ma.get_method()
                    sig = _signature(target)
                    target_id = method_sigs.get(sig) or external_method_id(sig)
                    call = calls_by_offset.get(offset)
                    if call is None:
                        continue
                    call["resolution_status"] = (
                        "xref_resolved" if sig in method_sigs else "xref_external"
                    )
                    tables["call_targets"].append(
                        {
                            "apk_sha256": expected,
                            "call_id": call["call_id"],
                            "target_method_id": target_id,
                            "target_signature": sig,
                            "target_kind": "internal" if sig in method_sigs else "external",
                            "evidence_source": "androguard-xref",
                            "resolution_policy": "androguard-4.1.4",
                        }
                    )

                if cfg.extraction.cfg and code is not None:
                    blocks = list(ma.get_basic_blocks().gets())
                    block_ids = {}
                    for block in blocks:
                        bid = stable_id("block", mid, block.get_start(), block.get_end())
                        block_ids[block] = bid
                        ids = [
                            iid
                            for off, iid in offset_to_iid.items()
                            if block.get_start() <= off < block.get_end()
                        ]
                        tables["basic_blocks"].append(
                            {
                                "apk_sha256": expected,
                                "block_id": bid,
                                "method_id": mid,
                                "start_offset": block.get_start(),
                                "end_offset": block.get_end(),
                                "instruction_ids": ids,
                                "handler_info_json": None,
                            }
                        )
                    for block in blocks:
                        for branch_src, branch_dst, child in block.get_next():
                            src, dst = block_ids[block], block_ids.get(child)
                            if dst is None:
                                continue
                            tables["cfg_edges"].append(
                                {
                                    "apk_sha256": expected,
                                    "cfg_edge_id": stable_id(
                                        "cfg-edge", mid, src, dst, branch_src, branch_dst
                                    ),
                                    "method_id": mid,
                                    "src_block_id": src,
                                    "dst_block_id": dst,
                                    "branch_kind": "androguard-successor",
                                    "evidence_instruction_id": instruction_lookup.get(
                                        (mid, branch_src)
                                    ),
                                }
                            )

    if cfg.extraction.entrypoints:
        methods_by_owner_name: dict[tuple[str, str], list[str]] = defaultdict(list)
        for row in tables["methods"]:
            methods_by_owner_name[(row["owner"], row["name"])].append(row["method_id"])
        targets_by_call: dict[str, list[dict]] = defaultdict(list)
        for row in tables["call_targets"]:
            targets_by_call[row["call_id"]].append(row)
        calls_by_caller: dict[str, list[dict]] = defaultdict(list)
        for row in tables["callsites"]:
            calls_by_caller[row["caller_method_id"]].append(row)
        for rows in calls_by_caller.values():
            rows.sort(key=lambda row: (row["offset_bytes"], row["call_id"]))

        traversal_policy = {
            "version": "entry-dfs-v1",
            "max_depth": 5,
            "max_tokens": 512,
            "external_prefixes": ["Landroid/", "Landroidx/", "Ljava/", "Ljavax/", "Ldalvik/"],
        }
        traversal_hash = hashlib.sha256(
            json.dumps(traversal_policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        for component in tables["components"]:
            ctype = component["component_type"]
            descriptor = "L" + component["name_normalized"].replace(".", "/") + ";"
            candidates: list[tuple[str, str]] = []
            for lifecycle in sorted(LIFECYCLE_METHODS.get(ctype, set())):
                candidates.extend(
                    (lifecycle, mid)
                    for mid in methods_by_owner_name.get((descriptor, lifecycle), [])
                )
            if not candidates:
                tables["entrypoints"].append(
                    {
                        "apk_sha256": expected,
                        "entry_id": stable_id("entry", expected, component["component_id"], None),
                        "component_ref": component["component_id"],
                        "method_id": None,
                        "entry_kind": f"component:{ctype}",
                        "discovery_rule": "manifest-lifecycle-v1",
                        "match_status": "unmatched",
                    }
                )
                continue
            for lifecycle, entry_mid in candidates:
                eid = stable_id("entry", expected, component["component_id"], entry_mid, lifecycle)
                tables["entrypoints"].append(
                    {
                        "apk_sha256": expected,
                        "entry_id": eid,
                        "component_ref": component["component_id"],
                        "method_id": entry_mid,
                        "entry_kind": f"{ctype}:{lifecycle}",
                        "discovery_rule": "manifest-lifecycle-v1",
                        "match_status": "matched",
                    }
                )
                tokens, provenance, truncated = _traverse_sequence(
                    entry_mid, calls_by_caller, targets_by_call, traversal_policy
                )
                sequence_id = stable_id("api-sequence", eid, traversal_hash)
                tables["api_sequences"].append(
                    {
                        "apk_sha256": expected,
                        "sequence_id": sequence_id,
                        "entry_id": eid,
                        "component_type": ctype,
                        "tokens": tokens,
                        "token_provenance_json": json.dumps(provenance, separators=(",", ":")),
                        "traversal_config_hash": traversal_hash,
                        "truncated": truncated,
                        "truncation_reason": "max_tokens" if truncated else None,
                    }
                )

    taxonomy_path = cfg.source_file.parent.parent / "assets/security-taxonomy-v1.json"
    taxonomy = json.loads(taxonomy_path.read_text())
    seen_knowledge_entities: set[str] = set()
    for target in tables["call_targets"]:
        if target["target_kind"] != "external":
            continue
        entity_id = target["target_method_id"]
        if entity_id in seen_knowledge_entities:
            continue
        seen_knowledge_entities.add(entity_id)
        signature = target["target_signature"]
        concepts = sorted(
            concept
            for concept, patterns in taxonomy["concepts"].items()
            if any(pattern in signature for pattern in patterns)
        )
        tables["knowledge_matches"].append(
            {
                "apk_sha256": expected,
                "entity_id": entity_id,
                "mapping_kind": "security-concept",
                "values": concepts,
                "knowledge_version": taxonomy["version"],
                "match_status": "matched" if concepts else "unmapped",
                "evidence_source": taxonomy_path.name,
            }
        )
        tables["knowledge_matches"].append(
            {
                "apk_sha256": expected,
                "entity_id": entity_id,
                "mapping_kind": "api-permission",
                "values": [],
                "knowledge_version": "pScout-Axplorer:not_provisioned",
                "match_status": "blocked_missing_asset",
                "evidence_source": "pScout/Axplorer asset absent",
            }
        )

    diagnostics = {
        "apk_sha256": expected,
        "zip": zip_diag,
        "analysis_mode": "declared_invoke_fallback" if analysis_fallback else "androguard_xref",
        "decode_warnings": decode_warnings,
        "counts": {name: len(rows) for name, rows in sorted(tables.items())},
        "manifest_xml_sha256": hashlib.sha256(etree.tostring(root)).hexdigest()
        if root is not None
        else None,
    }
    return dict(tables), diagnostics
