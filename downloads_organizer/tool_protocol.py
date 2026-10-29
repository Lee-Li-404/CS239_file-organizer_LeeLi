"""Conservative tool transport compatibility, independent of role/workflow policy.

Only assistant output is eligible. Never search prose or tool results for actions,
invent required arguments, discard unexpected keys, or change paths/command text.
"""

from __future__ import annotations

import copy
import json
import re
import uuid


class ToolProtocolError(ValueError):
    def __init__(self, code, *, path="$", missing=None):
        self.code, self.path, self.missing = code, path, missing
        super().__init__(code)

    def feedback(self):
        result = {"error": self.code, "at": self.path,
                  "message": "Correct the tool arguments to match the supplied schema. "
                             "Use ordinary JSON keys, supply all required fields at their declared level, "
                             "and omit undeclared arguments. Do not repeat the unchanged failed call."}
        if self.missing is not None:
            result.update(missing=self.missing, required=self.missing)
        return result


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ToolProtocolError("duplicate_argument_keys")
        result[key] = value
    return result


def _loads(text):
    def reject_constant(_):
        raise ToolProtocolError("invalid_tool_json")

    try:
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=reject_constant)
    except (ValueError, TypeError) as error:
        if isinstance(error, ToolProtocolError):
            raise
        raise ToolProtocolError("invalid_tool_json") from None


def _unquote(value):
    if isinstance(value, str) and value.startswith('"') and value.endswith('"'):
        try:
            decoded = _loads(value)
            if isinstance(decoded, str):
                return decoded
        except ToolProtocolError:
            pass
    return value


def _normalize(value, schema, repairs, path="$", *, tool=None):
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        result = {}
        for key, item in value.items():
            candidate = _unquote(key)
            normalized = candidate if candidate in properties else key
            if normalized in result:
                raise ToolProtocolError("conflicting_argument_keys", path=path)
            if normalized != key:
                repairs.append({"reason": "quoted_schema_key", "at": path + "." + normalized})
            # Unknown/free-form payloads remain data, including their key spellings.
            result[normalized] = (_normalize(item, properties[normalized], repairs,
                                             path + "." + normalized, tool=tool)
                                  if normalized in properties else item)
        return result
    if isinstance(value, list):
        return [_normalize(item, schema.get("items", {}), repairs, f"{path}[{i}]", tool=tool)
                for i, item in enumerate(value)]
    candidate = _unquote(value)
    # Repair only transport identifiers. Never unwrap filenames, argv, excerpts,
    # free text, hashes or arbitrary payload strings.
    identifier = (tool == "send_message" and path in
                  {"$.type", "$.payload.request_id", "$.payload.kind"})
    field_name = tool == "send_message" and re.fullmatch(r"\$\.payload\.fields\[\d+\]", path)
    if candidate != value and (("enum" in schema and candidate in schema["enum"])
            or ((identifier or field_name) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", candidate))):
        repairs.append({"reason": "quoted_transport_identifier", "at": path})
        return candidate
    return value


def _validate(value, schema, path="$"):
    """Validate the structural keywords used by this app's tool schemas."""
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "integer": type(value) is int,
               "boolean": type(value) is bool, "null": value is None}
    if kinds and not any(matches.get(kind, False) for kind in kinds):
        raise ToolProtocolError("invalid_argument_type", path=path)
    if "enum" in schema and value not in schema["enum"]:
        raise ToolProtocolError("invalid_argument_value", path=path)
    if isinstance(value, str) and (len(value) < schema.get("minLength", 0)
            or ("pattern" in schema and not re.search(schema["pattern"], value))):
        raise ToolProtocolError("invalid_argument_value", path=path)
    if isinstance(value, dict):
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ToolProtocolError("missing_tool_arguments", path=path, missing=missing)
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ToolProtocolError("unexpected_tool_arguments", path=path)
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], path + "." + key)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ToolProtocolError("invalid_argument_value", path=path)
        for i, item in enumerate(value):
            _validate(item, schema.get("items", {}), f"{path}[{i}]")
    if type(value) is int and (value < schema.get("minimum", value)
                               or value > schema.get("maximum", value)):
        raise ToolProtocolError("invalid_argument_value", path=path)


def arguments_for(name, raw, tools):
    definition = next((t["function"] for t in tools if t["function"]["name"] == name), None)
    if definition is None:
        raise ToolProtocolError("unknown_tool")
    repairs = []
    value = _loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
    if isinstance(value, str):
        value = _loads(value)
        repairs.append({"reason": "double_encoded_arguments", "at": "$"})
    if not isinstance(value, dict):
        raise ToolProtocolError("invalid_argument_type")
    value = _normalize(value, definition["parameters"], repairs, tool=name)
    _validate(value, definition["parameters"])
    return value, repairs


def normalize_message(message, tools):
    """Return effective assistant message, auditable repairs, and fallback error.

    Native calls win. Text fallback accepts a single whole JSON tool envelope
    (optionally fenced). The exact legacy send_message argument object is also
    accepted; no other bare object is inferred to be an action.
    """
    effective = copy.deepcopy(message)
    repairs = []
    if message.get("role") != "assistant" or message.get("refusal"):
        return effective, repairs, None
    if message.get("tool_calls"):
        seen = set()
        effective["tool_calls"] = [copy.deepcopy(call) for call in message["tool_calls"]]
        for call in effective["tool_calls"]:
            if not isinstance(call.get("id"), str) or not call["id"] or call["id"] in seen:
                call["id"] = "compat_" + uuid.uuid4().hex[:16]
                repairs.append({"reason": "assigned_call_id", "call_id": call["id"]})
            seen.add(call["id"])
            function = call.get("function", {})
            if isinstance(function.get("arguments"), dict):
                function["arguments"] = json.dumps(function["arguments"], ensure_ascii=False)
                repairs.append({"reason": "object_arguments", "call_id": call["id"]})
            try:
                value, changes = arguments_for(function.get("name"), function.get("arguments"), tools)
            except ToolProtocolError:
                continue  # Ordinary tool-result feedback will report the error.
            if changes:
                function["arguments"] = json.dumps(value, ensure_ascii=False)
                repairs.extend({**change, "call_id": call["id"]} for change in changes)
        return effective, repairs, None
    content = message.get("content")
    if not isinstance(content, str):
        return effective, repairs, None
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
    if fenced:
        text = fenced[1].strip()
    if not text.startswith("{"):
        return effective, repairs, None
    try:
        body = _loads(text)
        if not isinstance(body, dict):
            raise ToolProtocolError("invalid_tool_envelope")
        if set(body) == {"tool", "arguments"}:
            name, raw = body["tool"], body["arguments"]
        elif set(body) == {"tool"}:
            name = body["tool"]
            definition = next((t["function"] for t in tools if t["function"]["name"] == name), None)
            if definition is None:
                raise ToolProtocolError("unknown_tool")
            parameters = definition["parameters"]
            if parameters.get("properties") or parameters.get("required"):
                raise ToolProtocolError("missing_tool_arguments", missing=["arguments"])
            raw = {}  # A declared zero-parameter function needs no supplied values.
        elif {_unquote(key) for key in body} == {"recipient", "type", "payload"}:
            name, raw = "send_message", body
        else:
            raise ToolProtocolError("invalid_tool_envelope")
        value, changes = arguments_for(name, raw, tools)
    except ToolProtocolError as error:
        return effective, [], error.feedback()
    call_id = "compat_" + uuid.uuid4().hex[:16]
    effective = {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(value, ensure_ascii=False)}}]}
    repairs = [{"reason": "assistant_json_tool_fallback", "call_id": call_id,
                "legacy_message_arguments": "tool" not in body},
               *[{**change, "call_id": call_id} for change in changes]]
    if set(body) == {"tool"}:
        repairs.append({"reason": "zero_parameter_shorthand", "call_id": call_id})
    return effective, repairs, None
