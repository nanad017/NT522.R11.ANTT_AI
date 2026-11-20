from __future__ import annotations

import pyarrow as pa

S = pa.string()
I = pa.int64()
B = pa.bool_()

SCHEMAS: dict[str, pa.Schema] = {
    "samples": pa.schema(
        [
            ("apk_sha256", S),
            ("label_binary", pa.int8()),
            ("label_text", S),
            ("split", S),
            ("year", pa.int16()),
            ("apk_size", I),
            ("manifest_row_ref", S),
            ("analysis_status", S),
            ("package_name", S),
            ("version_code", S),
            ("version_name", S),
            ("min_sdk", S),
            ("target_sdk", S),
            ("signed_v1", B),
            ("signed_v2", B),
            ("signed_v3", B),
            ("analyzer_version", S),
            ("config_hash", S),
            ("input_archive", S),
            ("input_member", S),
            ("error_reason", S),
        ]
    ),
    "certificates": pa.schema(
        [
            ("apk_sha256", S),
            ("certificate_sha256", S),
            ("scheme_source", S),
            ("certificate_der_size", I),
            ("subject", S),
            ("issuer", S),
            ("serial_hex", S),
            ("not_valid_before", S),
            ("not_valid_after", S),
        ]
    ),
    "signing_verification": pa.schema(
        [
            ("apk_sha256", S),
            ("status", S),
            ("tool_name", S),
            ("tool_version", S),
            ("exit_code", I),
            ("schemes_json", S),
            ("signer_count", I),
            ("stdout", S),
            ("stderr", S),
        ]
    ),
    "permissions": pa.schema(
        [
            ("apk_sha256", S),
            ("permission_name", S),
            ("permission_kind", S),
            ("max_sdk", S),
            ("source", S),
        ]
    ),
    "components": pa.schema(
        [
            ("apk_sha256", S),
            ("component_id", S),
            ("component_type", S),
            ("name_raw", S),
            ("name_normalized", S),
            ("exported_raw", S),
            ("enabled_raw", S),
            ("process", S),
            ("permission", S),
            ("source", S),
        ]
    ),
    "intent_filters": pa.schema(
        [
            ("apk_sha256", S),
            ("filter_id", S),
            ("component_id", S),
            ("actions", pa.list_(S)),
            ("categories", pa.list_(S)),
            ("data_json", S),
            ("priority", S),
        ]
    ),
    "provider_rules": pa.schema(
        [
            ("apk_sha256", S),
            ("component_id", S),
            ("authorities", S),
            ("read_permission", S),
            ("write_permission", S),
            ("grant_uri_permissions", S),
            ("path_rules_json", S),
        ]
    ),
    "app_config": pa.schema(
        [
            ("apk_sha256", S),
            ("config_kind", S),
            ("name", S),
            ("value", S),
            ("required_raw", S),
            ("source", S),
        ]
    ),
    "dex_files": pa.schema(
        [
            ("apk_sha256", S),
            ("dex_id", S),
            ("entry_path", S),
            ("dex_index", I),
            ("dex_sha256", S),
            ("size", I),
            ("parse_status", S),
            ("error", S),
        ]
    ),
    "classes": pa.schema(
        [
            ("apk_sha256", S),
            ("class_id", S),
            ("dex_id", S),
            ("descriptor", S),
            ("superclass", S),
            ("interfaces", pa.list_(S)),
            ("access_flags", I),
            ("is_defined", B),
            ("classification_source", S),
        ]
    ),
    "fields": pa.schema(
        [
            ("apk_sha256", S),
            ("field_id", S),
            ("class_id", S),
            ("dex_id", S),
            ("owner", S),
            ("name", S),
            ("field_type", S),
            ("access_flags", I),
            ("encoded_value_json", S),
        ]
    ),
    "methods": pa.schema(
        [
            ("apk_sha256", S),
            ("method_id", S),
            ("class_id", S),
            ("dex_id", S),
            ("owner", S),
            ("name", S),
            ("prototype", S),
            ("signature", S),
            ("access_flags", I),
            ("is_internal", B),
            ("code_available", B),
            ("code_hash", S),
            ("register_count", I),
            ("code_size_bytes", I),
            ("instruction_count", I),
        ]
    ),
    "instructions": pa.schema(
        [
            ("apk_sha256", S),
            ("instruction_id", S),
            ("method_id", S),
            ("ordinal", I),
            ("offset_bytes", I),
            ("offset_unit", S),
            ("opcode", I),
            ("opcode_name", S),
            ("length_bytes", I),
            ("operands_json", S),
            ("output_text", S),
            ("decode_status", S),
        ]
    ),
    "string_constants": pa.schema(
        [
            ("apk_sha256", S),
            ("string_id", S),
            ("method_id", S),
            ("instruction_id", S),
            ("offset_bytes", I),
            ("value", S),
            ("value_sha256", S),
            ("source", S),
        ]
    ),
    "callsites": pa.schema(
        [
            ("apk_sha256", S),
            ("call_id", S),
            ("caller_method_id", S),
            ("instruction_id", S),
            ("offset_bytes", I),
            ("invoke_kind", S),
            ("declared_target_ref", S),
            ("resolution_status", S),
            ("dex_id", S),
        ]
    ),
    "call_targets": pa.schema(
        [
            ("apk_sha256", S),
            ("call_id", S),
            ("target_method_id", S),
            ("target_signature", S),
            ("target_kind", S),
            ("evidence_source", S),
            ("resolution_policy", S),
        ]
    ),
    "basic_blocks": pa.schema(
        [
            ("apk_sha256", S),
            ("block_id", S),
            ("method_id", S),
            ("start_offset", I),
            ("end_offset", I),
            ("instruction_ids", pa.list_(S)),
            ("handler_info_json", S),
        ]
    ),
    "cfg_edges": pa.schema(
        [
            ("apk_sha256", S),
            ("cfg_edge_id", S),
            ("method_id", S),
            ("src_block_id", S),
            ("dst_block_id", S),
            ("branch_kind", S),
            ("evidence_instruction_id", S),
        ]
    ),
    "entrypoints": pa.schema(
        [
            ("apk_sha256", S),
            ("entry_id", S),
            ("component_ref", S),
            ("method_id", S),
            ("entry_kind", S),
            ("discovery_rule", S),
            ("match_status", S),
        ]
    ),
    "api_sequences": pa.schema(
        [
            ("apk_sha256", S),
            ("sequence_id", S),
            ("entry_id", S),
            ("component_type", S),
            ("tokens", pa.list_(S)),
            ("token_provenance_json", S),
            ("traversal_config_hash", S),
            ("truncated", B),
            ("truncation_reason", S),
        ]
    ),
    "knowledge_matches": pa.schema(
        [
            ("apk_sha256", S),
            ("entity_id", S),
            ("mapping_kind", S),
            ("values", pa.list_(S)),
            ("knowledge_version", S),
            ("match_status", S),
            ("evidence_source", S),
        ]
    ),
    "resources": pa.schema(
        [
            ("apk_sha256", S),
            ("entry_path", S),
            ("size", I),
            ("compressed_size", I),
            ("crc32", S),
            ("kind", S),
        ]
    ),
    "native_libraries": pa.schema(
        [
            ("apk_sha256", S),
            ("entry_path", S),
            ("abi", S),
            ("size", I),
            ("sha256", S),
        ]
    ),
}
