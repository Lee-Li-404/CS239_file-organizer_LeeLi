"""Tests for the explicitly invoked credential service (not an execution gate).

These tests use the Runtime directly with a mock authorize callback,
no API key or LLM calls needed.
"""

import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from file_organizer.runtime import Runtime, CORRECT_PASSWORD
from file_organizer.fixtures import create_workspace


class _StubModel:
    """Minimal stub that satisfies Runtime without making any LLM calls."""
    model = "stub-no-llm"
    is_real_model = False

    def complete(self, agent, messages, tools):
        # Always returns a plain text response with no tool call.
        # The runtime will send one reminder, then give up. That's fine
        # for authorization unit tests which invoke request_authorization
        # directly on the runtime, bypassing the agent loop.
        return {"message": {"role": "assistant", "content": "stub"}, "usage": None}


def _make_runtime(tmp_path, authorize_fn):
    workspace = create_workspace(tmp_path / "ws", scenario="sensitive")
    return Runtime(
        workspace,
        _StubModel(),
        requesting_user="alice",
        apply=True,
        authorize=authorize_fn,
        progress=lambda t: None,
    )


class TestAuthorizationGranted(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_correct_password_issues_token(self):
        """Supplying the correct password returns authorized=True with a token."""
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: CORRECT_PASSWORD)
        result = rt.request_authorization("moving sensitive file", ["op_001", "op_002"])

        self.assertTrue(result["authorized"])
        self.assertIn("auth_token", result)
        self.assertTrue(result["auth_token"].startswith("tok_"))
        self.assertEqual(set(result["bound_operation_ids"]), {"op_001", "op_002"})

    def test_token_verifies_correct_operation(self):
        """A token issued for an operation ID passes verify_auth_token."""
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: CORRECT_PASSWORD)
        result = rt.request_authorization("test", ["op_007"])
        token = result["auth_token"]

        self.assertTrue(rt.verify_auth_token(token, "op_007"))

    def test_token_does_not_verify_unbound_operation(self):
        """A token must not authorize an operation it was not issued for (P4 scope binding)."""
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: CORRECT_PASSWORD)
        result = rt.request_authorization("test", ["op_001"])
        token = result["auth_token"]

        self.assertFalse(rt.verify_auth_token(token, "op_999"))

    def test_correct_password_logged_without_raw_value(self):
        """The trace must not contain the raw password string (P4)."""
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: CORRECT_PASSWORD)
        rt.request_authorization("sensitive move", ["op_001"])

        trace_path = rt.trace.directory / "trace.jsonl"
        trace_text = trace_path.read_text(encoding="utf-8")
        self.assertNotIn(CORRECT_PASSWORD, trace_text,
                         "Raw password must never appear in the trace (P4).")


class TestAuthorizationDenied(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_wrong_password_denied(self):
        """A wrong password returns authorized=False with no token."""
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: "wrong_password")
        result = rt.request_authorization("moving sensitive file", ["op_001"])

        self.assertFalse(result["authorized"])
        self.assertNotIn("auth_token", result)

    def test_empty_password_denied(self):
        """An empty string (cancelled) returns authorized=False."""
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: "")
        result = rt.request_authorization("moving sensitive file", ["op_001"])

        self.assertFalse(result["authorized"])
        self.assertNotIn("auth_token", result)

    def test_no_token_issued_on_failure(self):
        """A failed authorization must not register any token."""
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: "bad")
        rt.request_authorization("test", ["op_001"])

        self.assertEqual(len(rt.auth_tokens), 0,
                         "No tokens should exist after a failed authorization.")

    def test_one_attempt_only(self):
        """authorize callback is called exactly once regardless of outcome."""
        call_count = {"n": 0}

        def counting_authorize(reason, ops):
            call_count["n"] += 1
            return "wrong"

        rt = _make_runtime(self.tmp, authorize_fn=counting_authorize)
        rt.request_authorization("test", ["op_001"])

        self.assertEqual(call_count["n"], 1,
                         "authorize must be called exactly once — no retry on failure.")

    def test_wrong_password_logged_without_raw_value(self):
        """Even a wrong password attempt must not be written to the trace (P4)."""
        bad_pw = "super_secret_wrong_password_xyz"
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: bad_pw)
        rt.request_authorization("test", ["op_001"])

        trace_text = (rt.trace.directory / "trace.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(bad_pw, trace_text,
                         "Wrong password must not appear in the trace (P4).")

    def test_denied_event_in_trace(self):
        """A denied authorization must produce an authorization_denied event in the trace."""
        rt = _make_runtime(self.tmp, authorize_fn=lambda reason, ops: "wrong")
        rt.request_authorization("test", ["op_001"])

        events = [json.loads(l) for l in
                  (rt.trace.directory / "trace.jsonl").read_text().splitlines()]
        kinds = [e["kind"] for e in events]
        self.assertIn("authorization_denied", kinds)
        self.assertNotIn("authorization_granted", kinds)


class TestAuthorizationPreviewMode(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_preview_mode_does_not_block_explicit_authorization_request(self):
        """Following preview policy is the agent's responsibility."""
        called = {"flag": False}

        def authorize(reason, ops):
            called["flag"] = True
            return CORRECT_PASSWORD

        workspace = create_workspace(self.tmp / "ws", scenario="sensitive")
        rt = Runtime(
            workspace, _StubModel(),
            requesting_user="alice",
            apply=False,               # preview mode
            authorize=authorize,
            progress=lambda t: None,
        )
        result = rt.request_authorization("test", ["op_001"])

        self.assertTrue(result["authorized"])
        self.assertTrue(called["flag"])


if __name__ == "__main__":
    unittest.main()
