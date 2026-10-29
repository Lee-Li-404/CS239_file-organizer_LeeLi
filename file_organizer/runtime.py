"""Runtime for the multi-user shared workspace file organizer.

Policy instructions come exclusively from policy.md. Tools provide capabilities
and record observations; they do not enforce role, scope, or execution policies.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .tools import agent_tools, run_command
from .anonymizer import LocalAnonymizer, SUPPORTED_SUFFIXES

# Tool-call parsing reused from the existing project.
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from downloads_organizer.tool_protocol import ToolProtocolError, arguments_for, normalize_message

ROLES = ("interpreter", "planner", "analyzer", "arbiter", "executor")
GENERATED = {".organizer", ".anonymized"}


# ── Workspace helpers ─────────────────────────────────────────────────────────

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(root: Path) -> list[dict]:
    """List workspace metadata without filtering by requesting user."""
    root = root.resolve(strict=True)
    result = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        parts = rel.parts
        if any(p in GENERATED for p in parts) or not path.is_file():
            continue
        # Determine owner scope
        if parts[0] == "users" and len(parts) >= 3:
            owner = parts[1]
        elif parts[0] == "shared":
            owner = "shared"
        else:
            owner = "workspace"
        result.append({
            "file_id": f"file_{len(result) + 1:03d}",
            "path": rel.as_posix(),
            "size": path.stat().st_size,
            "owner": owner,
            "supported": path.suffix.lower() in SUPPORTED_SUFFIXES,
        })
    return result


# ── Trace ─────────────────────────────────────────────────────────────────────

class Trace:
    def __init__(self, directory: Path, run_id: str):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        directory.chmod(0o700)
        self.run_id = run_id
        self.seq = 0

    def event(self, kind: str, **fields) -> dict:
        self.seq += 1
        ev = {"event_id": f"E{self.seq:04d}", "run_id": self.run_id,
              "timestamp": datetime.now(timezone.utc).isoformat(),
              "kind": kind, **fields}
        with (self.directory / "trace.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev

    def save(self, name: str, data) -> None:
        target = self.directory / name
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)


# ── Runtime ───────────────────────────────────────────────────────────────────

def _interactive_authorize(reason: str, operation_ids: list[str]) -> str:
    """Default credential prompt used in real (non-test) runs."""
    import getpass
    print(f"\n[Authorization required] {reason}")
    print(f"  Operations: {', '.join(operation_ids)}")
    try:
        return getpass.getpass("  Enter your password (one attempt): ")
    except (EOFError, KeyboardInterrupt):
        return ""


# Synthetic credential for the optional authorization service, not a command gate.
CORRECT_PASSWORD = "correct"


class Runtime:
    def __init__(self, workspace: str | Path, model, *,
                 requesting_user: str,
                 apply: bool = False,
                 confirm=None,
                 authorize=None,
                 max_calls: int = 120,
                 max_steps: int = 64,
                 known_names=(),
                 progress=None):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be a directory.")
        self.model = model
        self.requesting_user = requesting_user
        self.apply = apply
        self.confirm = confirm or (lambda plan: False)
        # authorize(reason, operation_ids) → password string (empty = cancelled/wrong).
        # One attempt only: if the returned string is falsy, authorization is denied.
        self.authorize = authorize if authorize is not None else _interactive_authorize
        self.progress = progress or (lambda t: None)
        self.max_calls = max_calls
        self.max_steps = max_steps
        self.calls = 0
        self.run_id = "run_" + uuid.uuid4().hex[:16]

        run_dir = self.workspace / ".organizer" / "runs" / self.run_id
        self.trace = Trace(run_dir, self.run_id)
        self.anonymizer = LocalAnonymizer(self.workspace, known_names)

        self.queue: deque = deque()
        self.plans: list = []
        self.moves: list = []
        self.auth_tokens: dict[str, set] = {}  # token → set of operation_ids

        # Build per-role histories with system prompts.
        prompts_dir = Path(__file__).parent / "prompts"
        common = (prompts_dir / "common.md").read_text(encoding="utf-8")
        policy = (Path(__file__).parent / "policy.md").read_text(encoding="utf-8")
        self.histories = {}
        for role in ROLES:
            role_prompt = (prompts_dir / f"{role}.md").read_text(encoding="utf-8")
            self.histories[role] = [{
                "role": "system", "content": common + "\n" + policy + "\n" + role_prompt,
            }]

        self.state = {
            "run_id": self.run_id,
            "workspace": str(self.workspace),
            "requesting_user": requesting_user,
            "model": model.model,
            "real_llm": model.is_real_model,
            "phase": 1,
            "policy_enforcement": "prompt_only_phase1",
            "status": "running",
            "plans": self.plans,
            "moves": self.moves,
            "model_calls": 0,
            "max_calls": self.max_calls,
            "max_steps_per_activation": self.max_steps,
        }
        self.trace.event("run_started", **{k: v for k, v in self.state.items()
                                           if k not in ("plans", "moves")})
        self._save()

    def _save(self):
        self.state["model_calls"] = self.calls
        self.trace.save("manifest.json", self.state)

    # ── Message routing ───────────────────────────────────────────────────────

    def send(self, sender: str, recipient: str, type: str, payload: dict) -> dict:
        if recipient not in (*ROLES, "runtime") or not isinstance(payload, dict):
            raise ValueError("Invalid recipient or payload.")
        msg = {
            "run_id": self.run_id,
            "message_id": "msg_" + uuid.uuid4().hex[:12],
            "sender": sender, "recipient": recipient,
            "type": type, "payload": payload,
        }
        self.trace.event("agent_message", message=msg)
        if recipient != "runtime":
            self.queue.append((recipient, msg))
        return {"message_id": msg["message_id"], "queued": recipient != "runtime"}

    # ── Authorization tokens ──────────────────────────────────────────────────

    def request_authorization(self, reason: str, operation_ids: list) -> dict:
        """Ask the authorize callback for a credential. One attempt only.

        The callback receives (reason, operation_ids) and returns a password
        string. Any non-empty string that matches CORRECT_PASSWORD is accepted;
        an empty string or wrong value is denied immediately — no retry.

        The raw password is never stored or logged. Only the outcome and an
        opaque token (on success) are written to the trace.
        This service does not inspect plans or prevent unauthorized commands.
        """
        password = self.authorize(reason, operation_ids)

        # One attempt: wrong or empty → denied, no second chance.
        if not password or password.strip() != CORRECT_PASSWORD:
            self.trace.event("authorization_denied",
                             reason=reason, operation_ids=operation_ids,
                             note="incorrect or empty credential; raw value never logged")
            return {"authorized": False,
                    "reason": "incorrect credential — operation not authorized"}

        token = "tok_" + uuid.uuid4().hex[:20]
        self.auth_tokens[token] = set(operation_ids)
        self.trace.event("authorization_granted",
                         reason=reason, operation_ids=operation_ids,
                         token_issued=token,
                         note="credential accepted; raw password never logged")
        return {"authorized": True,
                "auth_token": token,
                "bound_operation_ids": operation_ids}

    def verify_auth_token(self, token: str, operation_id: str) -> bool:
        """Report token membership; this helper is never an execution gate."""
        return token in self.auth_tokens and operation_id in self.auth_tokens[token]

    # ── Plan management ───────────────────────────────────────────────────────

    def submit_plan(self, summary: str, operations: list) -> dict:
        if not isinstance(summary, str) or not isinstance(operations, list):
            raise ValueError("Invalid plan structure.")
        registered = []
        for i, op in enumerate(operations, 1):
            if not isinstance(op, dict) or not all(
                isinstance(op.get(k), str) for k in ("source", "destination", "reason")
            ):
                raise ValueError(f"Operation {i} missing required string fields.")
            entry = dict(op, operation_id=f"op_{i:03d}")
            src = self.workspace / op["source"]
            entry["source_sha256"] = digest(src) if src.is_file() else None
            # Scope labels are observations, not admission checks.
            entry["source_scope"] = self._scope(op["source"])
            entry["dest_scope"] = self._scope(op["destination"])
            registered.append(entry)
        content = {"summary": summary, "operations": registered}
        plan = {
            **content,
            "plan_id": "plan_" + uuid.uuid4().hex[:12],
            "version": len(self.plans) + 1,
            "digest": hashlib.sha256(
                json.dumps(content, sort_keys=True).encode()
            ).hexdigest(),
            "confirmed": False,
        }
        self.plans.append(plan)
        self.trace.event("plan_proposed", plan=plan)
        self._save()
        self.progress(f"Plan v{plan['version']}: {len(registered)} operation(s).")
        return {
            "plan_id": plan["plan_id"],
            "version": plan["version"],
            "digest": plan["digest"],
            "confirmed": plan["confirmed"],
            "preview_only": not self.apply,
            "pending_operation_ids": self._pending_ops(plan),
        }

    def request_confirmation(self, plan_id: str, version: int) -> dict:
        """Offer confirmation when explicitly requested; never gate commands."""
        result = self.get_plan(plan_id, version)
        if result.get("error"):
            return result
        plan = result["plan"]
        plan["confirmed"] = self.confirm(plan) is True
        self.trace.event("confirmation", plan_id=plan_id, version=version,
                         accepted=plan["confirmed"])
        self._save()
        return {"plan_id": plan_id, "version": version,
                "confirmed": plan["confirmed"]}

    def _scope(self, path: str) -> str:
        parts = Path(path).parts
        if parts and parts[0] == "users" and len(parts) >= 2:
            return parts[1]
        if parts and parts[0] == "shared":
            return "shared"
        return "workspace"

    def _pending_ops(self, plan: dict) -> list[str]:
        pending = []
        for op in plan["operations"]:
            src = self.workspace / op["source"]
            dst = self.workspace / op["destination"]
            done = (not src.exists() and dst.is_file()
                    and digest(dst) == op.get("source_sha256"))
            if not done:
                pending.append(op["operation_id"])
        return pending

    def get_plan(self, plan_id: str, version: int) -> dict:
        plan = next((p for p in self.plans
                     if p["plan_id"] == plan_id and p["version"] == version), None)
        if plan is None:
            return {"error": "unknown_plan"}
        return {
            "plan": plan,
            "current": self.plans[-1] is plan,
            "execution_enabled": self.apply,
            "confirmed": plan["confirmed"],
            "pending_operation_ids": self._pending_ops(plan),
        }

    # ── Tool dispatch ─────────────────────────────────────────────────────────

    def _move_intent(self, argv):
        if not isinstance(argv, list) or not argv or Path(argv[0]).name != "mv":
            return None
        args = [v for v in argv[1:] if v not in ("--", "-n")]
        if len(args) != 2 or any(v.startswith("-") for v in args):
            return None
        src = (self.workspace / args[0]).resolve()
        dst = (self.workspace / args[1]).resolve()
        if not all(p.is_relative_to(self.workspace) for p in (src, dst)):
            return None
        if not src.is_file() or dst.exists():
            return None
        return {"source": src.relative_to(self.workspace).as_posix(),
                "destination": dst.relative_to(self.workspace).as_posix(),
                "sha256": digest(src)}

    def tool(self, actor: str, name: str, arguments: dict) -> dict:
        if name == "send_message":
            return self.send(actor, **arguments)
        if name == "submit_plan":
            return self.submit_plan(**arguments)
        if name == "get_plan":
            return self.get_plan(**arguments)
        if name == "request_confirmation":
            return self.request_confirmation(**arguments)
        if name == "request_authorization":
            return self.request_authorization(**arguments)
        if name == "list_files":
            return {"inventory": inventory(self.workspace)}
        if name == "anonymize_file":
            return self.anonymizer.anonymize_file(**arguments)
        if name == "upload_file":
            path = self.workspace / arguments["file"]
            if path.stat().st_size > 2_000_000:
                raise ValueError("File too large.")
            content = path.read_text(encoding="utf-8")
            if len(content) > 16000:
                raise ValueError("Content exceeds 16 000 characters.")
            return {"selected_file": arguments["file"], "content": content,
                    "transfer": "next_model_request"}
        if name == "run_command":
            intent = self._move_intent(arguments.get("argv"))
            if intent:
                self.trace.event("move_intent", actor=actor, **intent)
            result = run_command(self.workspace, **arguments)
            if intent:
                src = self.workspace / intent["source"]
                dst = self.workspace / intent["destination"]
                if not src.exists() and dst.is_file() and digest(dst) == intent["sha256"]:
                    self.moves.append({**intent, "undone": False})
                    self.trace.event("move_completed", actor=actor, **intent)
                    self._save()
            return result
        raise ValueError(f"Unhandled tool: {name}")

    # ── Agent activation loop ─────────────────────────────────────────────────

    def activate(self, role: str, message: dict):
        history = self.histories[role]
        history.append({"role": "user",
                        "content": json.dumps(message, ensure_ascii=False)})
        tools = agent_tools(role)
        self.progress(f"{role}: {message.get('type', '?')}")
        handed_off = False
        reminded = False

        for _ in range(self.max_steps):
            if self.calls >= self.max_calls:
                raise RuntimeError("Global model-call budget reached.")
            self.calls += 1
            self.trace.event("model_request", agent=role,
                             messages=history, tools=tools)
            response = self.model.complete(role, history, tools)
            self.trace.event("model_response", agent=role, **response)

            assistant, repairs, fallback_error = normalize_message(
                response["message"], tools)
            if repairs:
                self.trace.event("tool_call_compat", agent=role, repairs=repairs)
            history.append(assistant)

            if assistant.get("refusal"):
                return

            requests = assistant.get("tool_calls", [])
            if not requests:
                if not reminded and not handed_off:
                    note = ("Runtime: your response performed no action. "
                            "Use a native tool call or emit one JSON envelope "
                            '{"tool":"NAME","arguments":{...}}.')
                    if fallback_error:
                        note += " Protocol error: " + json.dumps(fallback_error)
                    history.append({"role": "user", "content": note})
                    reminded = True
                    continue
                return

            for req in requests:
                fn = req["function"]
                self.trace.event("tool_request", agent=role,
                                 call_id=req["id"], **fn)
                try:
                    args, _ = arguments_for(fn["name"], fn["arguments"], tools)
                    result = self.tool(role, fn["name"], args)
                except ToolProtocolError as e:
                    result = e.feedback()
                except (ValueError, TypeError, OSError,
                        UnicodeError, subprocess.SubprocessError) as e:
                    result = {"error": type(e).__name__,
                              "message": "Tool failed; check arguments."}
                ev = self.trace.event("tool_result", agent=role,
                                      call_id=req["id"], tool=fn["name"],
                                      result=result)
                result = {**result, "tool_record_id": ev["event_id"]}
                if fn["name"] in {"send_message", "submit_plan"} and not result.get("error"):
                    handed_off = True
                history.append({"role": "tool", "tool_call_id": req["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})

    # ── Top-level run ─────────────────────────────────────────────────────────

    def run(self, instruction: str) -> dict:
        try:
            # Kick off with Interpreter.
            self.send("runtime", "interpreter", "user_instruction", {
                "instruction": instruction,
                "requesting_user": self.requesting_user,
                "workspace_layout": {
                    "private": f"users/{self.requesting_user}/",
                    "shared": "shared/",
                },
                "execution_enabled": self.apply,
            })
            while self.queue:
                role, message = self.queue.popleft()
                self.activate(role, message)

            confirmed = [p for p in self.plans if p["confirmed"]]
            if confirmed:
                latest = confirmed[-1]
                complete = all(
                    not (self.workspace / op["source"]).exists()
                    and (self.workspace / op["destination"]).is_file()
                    and digest(self.workspace / op["destination"]) == op.get("source_sha256")
                    for op in latest["operations"]
                )
                status = "completed" if complete else "incomplete"
            elif not self.plans:
                status = "no_plan"
            else:
                status = "preview_ready" if not self.apply else "not_executed"

            self.state["status"] = status
            self.trace.event("run_finished", status=status,
                             model_calls=self.calls, moves=len(self.moves))
            self._save()
            return self.state
        except BaseException as e:
            self.state["status"] = "failed"
            self.trace.event("run_failed", error_type=type(e).__name__)
            self._save()
            raise
