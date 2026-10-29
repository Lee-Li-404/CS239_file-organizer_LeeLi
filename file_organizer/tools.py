"""Tool schemas and subprocess runner for the multi-user file organizer."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from pathlib import Path


def schema(name, description, properties, required, additional=False):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": additional},
    }}


STRING_LIST = {"type": "array", "items": {"type": "string"}}

# ── send_message ──────────────────────────────────────────────────────────────
PAYLOAD = {"type": "object", "properties": {
    "request_id": {"type": "string"}, "kind": {"type": "string"},
    "reason": {"type": "string"}, "instruction": {"type": "string"},
    "files": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "unsupported": STRING_LIST, "uncertain": STRING_LIST,
    "plan_id": {"type": "string"}, "version": {"type": "integer"},
    "digest": {"type": "string"},
    "pending_operation_ids": STRING_LIST, "completed_operation_ids": STRING_LIST,
    "remaining_operation_ids": STRING_LIST, "next_pending_operation_ids": STRING_LIST,
    "conflicting_operations": {"type": "array", "items": {"type": "object"}},
    "blocked": {"type": "boolean"}, "observations": {"type": "string"},
    "auth_token": {"type": "string"},
}, "additionalProperties": True}

MESSAGE = schema(
    "send_message",
    "Send a structured message to another agent. "
    "recipient/type/payload are sibling top-level arguments.",
    {"recipient": {"type": "string",
                   "enum": ["interpreter", "planner", "analyzer", "arbiter", "executor", "runtime"]},
     "type": {"type": "string"}, "payload": PAYLOAD},
    ["recipient", "type", "payload"],
)

# ── submit_plan ───────────────────────────────────────────────────────────────
OPERATION = {"type": "object", "properties": {
    "source": {"type": "string"}, "destination": {"type": "string"},
    "reason": {"type": "string"},
    "evidence_refs": {"type": "array", "items": {"type": "string"}},
    "requires_auth": {"type": "boolean"},
    "auth_token": {"type": "string"},
}, "required": ["source", "destination", "reason", "evidence_refs"],
   "additionalProperties": False}

PLAN = schema(
    "submit_plan",
    "Register a proposed set of file operations and return its ID and digest. "
    "Does not request confirmation or dispatch work.",
    {"summary": {"type": "string"},
     "operations": {"type": "array", "items": OPERATION}},
    ["summary", "operations"],
)

# ── list_files ────────────────────────────────────────────────────────────────
LIST_FILES = schema(
    "list_files",
    "List all files in the workspace with IDs, relative paths, sizes, "
    "owner (user scope), and supported-format flag. Does not read file bodies.",
    {}, [],
)

# ── run_command ───────────────────────────────────────────────────────────────
COMMAND = schema(
    "run_command",
    "Run an argv command in the workspace; no implicit shell. "
    "Provide a natural-language description of the command's purpose and effect.",
    {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
     "description": {"type": "string", "minLength": 1, "pattern": r"\S"}},
    ["argv", "description"],
)

# ── anonymize_file ────────────────────────────────────────────────────────────
ANONYMIZE = schema(
    "anonymize_file",
    "Create a local anonymized text excerpt from a supported text or PDF file. "
    "Returns an artifact_id and output_file path.",
    {"path": {"type": "string"},
     "max_chars": {"type": "integer", "minimum": 512, "maximum": 12000}},
    ["path"],
)

# ── upload_file ───────────────────────────────────────────────────────────────
UPLOAD = schema(
    "upload_file",
    "Select a UTF-8 file to include as the next model tool result.",
    {"file": {"type": "string"}}, ["file"],
)

# ── get_plan ──────────────────────────────────────────────────────────────────
GET_PLAN = schema(
    "get_plan",
    "Retrieve the registered plan and its confirmation/authorization status. "
    "Returns pending operation IDs for the current batch.",
    {"plan_id": {"type": "string"}, "version": {"type": "integer", "minimum": 1}},
    ["plan_id", "version"],
)

# ── request_authorization ─────────────────────────────────────────────────────
REQUEST_AUTH = schema(
    "request_authorization",
    "Ask Runtime to prompt the user for a password to authorize specific operations. "
    "Runtime returns an opaque token bound to this run and these operation IDs.",
    {"reason": {"type": "string", "minLength": 1},
     "operation_ids": STRING_LIST},
    ["reason", "operation_ids"],
)

CONFIRM = schema(
    "request_confirmation",
    "Ask the user to confirm a registered plan and record their response.",
    {"plan_id": {"type": "string"}, "version": {"type": "integer", "minimum": 1}},
    ["plan_id", "version"],
)

# Every role receives every capability. Usage rules live in policy.md.
_ALL_TOOLS = [MESSAGE, PLAN, REQUEST_AUTH, GET_PLAN, CONFIRM,
              LIST_FILES, COMMAND, ANONYMIZE, UPLOAD]

_KNOWN_ROLES = {"interpreter", "planner", "analyzer", "arbiter", "executor"}


def agent_tools(role: str) -> list:
    if role not in _KNOWN_ROLES:
        raise ValueError(f"Unknown agent role: {role!r}")
    # Phase 1: identical tool set for every role.
    return list(_ALL_TOOLS)


def run_command(workspace: Path, argv: list[str], description: str,
                timeout: float = 15, output_limit: int = 16000) -> dict:
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise ValueError("argv must be a non-empty list of strings.")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("A non-blank description is required.")
    env = {k: os.environ[k] for k in ("PATH", "TMPDIR", "SYSTEMROOT") if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env.update(LANG="C", LC_ALL="C")
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(
            argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, start_new_session=True,
        )
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
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "truncated": any(len(d) > output_limit for d in data),
    }
