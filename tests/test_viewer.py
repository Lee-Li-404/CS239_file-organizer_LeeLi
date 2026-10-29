import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from downloads_organizer.viewer import ASSETS, ViewerHandler, load_run


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trace = self.root / "trace.jsonl"
        self.trace.write_text('{"kind":"run_started","run_id":"test"}\n')

    def test_explicit_run_loads_only_known_sidecars_not_paths_inside_data(self):
        (self.root / "manifest.json").write_text(json.dumps({"run_id": "test", "source_trace": "/not/a/selected/path"}))
        (self.root / ".env").write_text("FAKE_SECRET_MUST_NOT_BE_READ")
        result = load_run(self.root)
        self.assertEqual(list(result["sidecars"]), ["manifest.json"])
        self.assertNotIn("FAKE_SECRET", json.dumps(result))
        self.assertEqual(result["trace_text"], self.trace.read_text())

    def test_invalid_optional_sidecar_is_reported_without_losing_trace(self):
        (self.root / "cost_estimate.json").write_text("{bad json")
        result = load_run(self.trace)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("run_started", result["trace_text"])
        self.assertNotIn("cost_estimate.json", result["sidecars"])

    def test_implicit_symlink_sidecars_and_traces_are_not_followed(self):
        outside = self.root / "other.json"
        outside.write_text('{"secret":"FAKE_PRIVATE"}')
        (self.root / "manifest.json").symlink_to(outside)
        result = load_run(self.root)
        self.assertNotIn("FAKE_PRIVATE", json.dumps(result))
        self.assertEqual(len(result["warnings"]), 1)
        self.trace.unlink()
        self.trace.symlink_to(outside)
        with self.assertRaises(ValueError):
            load_run(self.root)

    def handler(self, route, host="127.0.0.1:8765", origin=None):
        handler = ViewerHandler.__new__(ViewerHandler)
        handler.path = route
        handler.headers = {"Host": host}
        if origin:
            handler.headers["Origin"] = origin
        handler.server = SimpleNamespace(server_port=8765, bootstrap=b'{"bundles":[]}')
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.send_error = Mock()
        handler.do_GET()
        return handler

    def test_http_serves_only_static_assets_and_explicit_bootstrap(self):
        handler = self.handler("/api/bootstrap")
        handler.send_response.assert_called_once_with(200)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"bundles": []})
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        for path in ["/.env", "/../.env", "/api/file?path=/etc/passwd", "/%2e%2e/.env"]:
            denied = self.handler(path)
            denied.send_error.assert_called_once_with(404)
            self.assertEqual(denied.wfile.getvalue(), b"")

    def test_http_rejects_foreign_hosts_and_origins(self):
        self.handler("/", host="untrusted.example:8765").send_error.assert_called_once_with(403, "Loopback host required")
        self.handler("/api/bootstrap", origin="https://untrusted.example").send_error.assert_called_once_with(403, "Same origin required")

    def test_all_local_assets_exist_and_frontend_has_no_html_injection_sink(self):
        for name in ["index.html", "style.css", "network.css", "network.js", "chat.css", "chat.js", "app.js", "trace-core.js", "demo.json"]:
            self.assertTrue((ASSETS / name).is_file())
        for filename in ("app.js", "network.js", "chat.js"):
            script = (ASSETS / filename).read_text()
            self.assertNotIn("innerHTML", script)
            self.assertNotIn("eval(", script)
        html = (ASSETS / "index.html").read_text()
        self.assertNotIn("https://", html)

    def test_demo_budget_sidecars_are_loaded_and_network_assets_are_served(self):
        (self.root / "demo_settings.json").write_text('{"max_calls":80}')
        (self.root / "demo_summary.json").write_text('{"model_calls":80}')
        result = load_run(self.root)
        self.assertEqual(result["sidecars"]["demo_settings.json"]["max_calls"], 80)
        for route in ("/network.js", "/network.css", "/chat.js", "/chat.css"):
            response = self.handler(route)
            response.send_response.assert_called_once_with(200)
            self.assertTrue(response.wfile.getvalue())


if __name__ == "__main__":
    unittest.main()
