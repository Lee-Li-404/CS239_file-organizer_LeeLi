"""Tool definitions and bounded subprocess I/O; not a Phase 2 policy engine."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from pathlib import Path

from .anonymizer import TOOL_SCHEMA


def schema(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False},
    }}


COMMAND = schema("run_command", "Run argv in the workspace; no implicit shell. "
                 "Provide a natural-language description of this command's purpose and effect. "
                 "Phase 1 does NOT enforce command, path or plan policies.",
                 {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                  "description": {"type": "string", "minLength": 1, "pattern": r"\S",
                                  "description": "Brief natural-language purpose and expected effect of this exact command. "
                                                 "Do not include secrets or claim it has already succeeded."}},
                 ["argv", "description"])
STRING_LIST = {"type": "array", "items": {"type": "string"}}
# Shared transport fields, not role permissions or a workflow state machine.
# Named properties help providers generate nested JSON without quoted-as-data
# keys. They remain optional: policy determines which fields a message needs.
FILE_EVIDENCE = {"type": "object", "properties": {
    "file_id": {"type": "string"}, "path": {"type": "string"},
    "size": {"type": "integer"}, "supported": {"type": "boolean"},
    "sha256": {"type": "string"}, "evidence_refs": STRING_LIST,
    "category": {"type": "string"}, "course": {"type": ["string", "null"]},
    "classification_basis": {"type": "string"}, "classification_reason": {"type": "string"},
    "reason": {"type": "string"}, "anonymized_excerpt": {"type": "string"},
    "artifact_id": {"type": "string"}, "status": {"type": "string"}}, "additionalProperties": True}
PAYLOAD = {"type": "object", "properties": {
    "request_id": {"type": "string"}, "kind": {"type": "string"}, "reason": {"type": "string"},
    "fields": STRING_LIST, "files": {"type": "array", "items": FILE_EVIDENCE},
    "unsupported": STRING_LIST, "uncertain": STRING_LIST,
    "plan_id": {"type": "string"}, "version": {"type": "integer"}, "digest": {"type": "string"},
    "pending_operation_ids": STRING_LIST, "completed_operation_ids": STRING_LIST,
    "remaining_operation_ids": STRING_LIST, "next_pending_operation_ids": STRING_LIST,
    "conflicting_operations": {"type": "array", "items": {"type": "object"}},
    "blocked": {"type": "boolean"}, "observations": {"type": "string"}}, "additionalProperties": True}
MESSAGE = schema("send_message", "Send a structured message; runtime binds your real identity. "
                 "recipient/type/payload are sibling arguments; request_id/kind/files go inside payload. "
                 "Use ordinary JSON keys/values, with no embedded quote characters or tool markers.",
                 {"recipient": {"type": "string", "enum": ["analyzer", "planner", "executor", "runtime"]},
                  "type": {"type": "string"}, "payload": PAYLOAD},
                 ["recipient", "type", "payload"])
UPLOAD = schema("upload_file", "Select a UTF-8 file to include as the next model tool result. "
                "Select anonymize_file's output. Phase 1 does NOT enforce output provenance.",
                {"file": {"type": "string"}}, ["file"])
PLAN = schema("submit_plan", "Register a proposal for preview and user confirmation. "
              "Does not dispatch work: after confirmation, explicitly message Executor. "
              "No policy assurance is installed in Phase 1.",
              {"summary": {"type": "string"}, "operations": {
                  "type": "array", "items": {"type": "object", "properties": {
                      "source": {"type": "string"}, "destination": {"type": "string"},
                      "reason": {"type": "string"},
                      "evidence_refs": {"type": "array", "items": {"type": "string"}}},
                      "required": ["source", "destination", "reason", "evidence_refs"],
                      "additionalProperties": False}}}, ["summary", "operations"])
LIST_FILES = schema("list_files", "List workspace file IDs, relative paths, sizes and supported formats. "
                    "Does not read or upload file bodies.", {}, [])
GET_PLAN = schema("get_plan", "Retrieve the runtime's registered plan and confirmation status. "
                  "Returns pending operation IDs for this batch.",
                  {"plan_id": {"type": "string"}, "version": {"type": "integer", "minimum": 1}},
                  ["plan_id", "version"])


def agent_tools(role: str) -> list:
    if role not in {"analyzer", "planner", "executor"}:
        raise ValueError("Unknown agent role.")
    # Phase 1: identical capabilities. Role permissions live in the policies,
    # not in tool filtering. A prohibited call remains possible and auditable.
    return [MESSAGE, PLAN, LIST_FILES, COMMAND, UPLOAD, GET_PLAN,
            {"type": "function", "function": TOOL_SCHEMA}]


def run_command(workspace: Path, argv: list[str], description: str, timeout: float = 15,
                output_limit: int = 16000) -> dict:
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise ValueError("argv must be a non-empty string array.")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("A nonblank natural-language command description is required.")
    # Basic credential hygiene is kept in both phases. This does NOT sandbox
    # processes or constrain filesystem access by the current OS user.
    env = {k: os.environ[k] for k in ("PATH", "TMPDIR", "SYSTEMROOT")
           if k in os.environ}
    env.setdefault("PATH", os.defpath)
    # C exists on macOS and Linux; inherited C.UTF-8 can break macOS shasum.
    env.update(LANG="C", LC_ALL="C")
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        data = []
        for stream in (out, err):
            stream.seek(0)
            data.append(stream.read(output_limit + 1))
    return {
        "stdout": data[0][:output_limit].decode("utf-8", errors="replace"),
        "stderr": data[1][:output_limit].decode("utf-8", errors="replace"),
        "exit_code": process.returncode, "timed_out": timed_out,
        "truncated": any(len(value) > output_limit for value in data),
    }
