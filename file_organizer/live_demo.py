"""Loopback live demo using fresh synthetic workspaces and the existing viewer."""
from __future__ import annotations

import argparse
import json
import secrets
import threading
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from downloads_organizer.viewer import ASSETS, ViewerHandler, load_run
from http.server import ThreadingHTTPServer
from .fixtures import create_workspace
from .runtime import RunCancelled, Runtime, digest, inventory

PRESETS = {
    'normal': 'Organize Alice’s files',
    'cross_boundary': 'Share a team document',
    'sensitive': 'Review synthetic credentials',
    'injection': 'Document with embedded instructions',
}
SUBMITTED = {
    '01-normal': '01 · Normal organization (10 files)',
    '02-injection-preview': '02 · Injection in preview',
    '03-sharing-authorization-denied': '03 · Sharing: authorization denied',
    '04-sharing-authorization-correct': '04 · Sharing: correct password',
    '05-cross-user-copy': '05 · Alice requests Bob’s private file',
}


def visible_workspace(workspace):
    """Observe Alice/shared paths for display; never read file contents into the UI."""
    workspace = Path(workspace)
    rows = []
    for scope in ['users/alice', 'shared']:
        root = workspace / scope
        if root.is_symlink() or (workspace / 'users').is_symlink():
            continue
        for path in sorted(root.rglob('*')):
            try:
                if path.is_symlink():
                    continue
                if path.is_dir():
                    rows.append(dict(path=str(path.relative_to(workspace)), kind='directory'))
                elif path.is_file():
                    rows.append(dict(path=str(path.relative_to(workspace)), kind='file',
                                     size=path.stat().st_size, sha256=digest(path)))
            except OSError:
                # A file can disappear between a directory listing and an observation.
                continue
    return rows


def workspace_changes(before, after):
    old = {r['path']: r for r in before if r['kind'] == 'file'}
    new = {r['path']: r for r in after if r['kind'] == 'file'}
    removed = set(old) - set(new)
    added = set(new) - set(old)
    pairs = {}
    for source in removed:
        destinations = [p for p in added if new[p]['sha256'] == old[source]['sha256']]
        sources = [p for p in removed if old[p]['sha256'] == old[source]['sha256']]
        if len(destinations) == len(sources) == 1:
            pairs[source] = destinations[0]
    changes = []
    for source in sorted(removed):
        changes.append(dict(path=source, change='moved out' if source in pairs else 'removed',
                            peer=pairs.get(source)))
    for path in sorted(added):
        source = next((s for s, d in pairs.items() if d == path), None)
        changes.append(dict(path=path, change='moved in' if source else 'added', peer=source))
    for path in sorted(set(old) & set(new)):
        if old[path]['sha256'] != new[path]['sha256']:
            changes.append(dict(path=path, change='modified', peer=None))
    return dict(before=before, after=after, changes=changes)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


class LiveDemo:
    def __init__(self, directory, env_file, model_factory=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.env_file = Path(env_file)
        self.model_factory = model_factory
        self.condition = threading.Condition()
        self.runtime = None
        self.model = None
        self.cancel = threading.Event()
        self.job_id = None
        self.active = False
        self.status = 'ready'
        self.error = None
        self.pending = None
        self.answer = None
        self.workspace = None
        self.before_visible = []
        self.preset_views = {}
        with tempfile.TemporaryDirectory(prefix='organizer-preset-preview-') as temp:
            for preset in PRESETS:
                rows = visible_workspace(create_workspace(Path(temp) / preset, preset))
                self.preset_views[preset] = workspace_changes(rows, rows)
        self.token = secrets.token_urlsafe(32)

    def start(self, instruction, preset, apply):
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError('Enter an instruction.')
        if preset not in PRESETS or not isinstance(apply, bool):
            raise ValueError('Invalid workspace preset or mode.')
        with self.condition:
            if self.active:
                raise ValueError('A demo is already running.')
            root = self.directory / uuid.uuid4().hex
            self.job_id = root.name
            self.cancel = threading.Event()
            self.model = None
            self.workspace = create_workspace(root / 'workspace', preset)
            self.before_visible = visible_workspace(self.workspace)
            self.runtime = None
            self.error = None
            self.pending = None
            self.answer = None
            self.active = True
            self.status = 'starting'
            threading.Thread(target=self._run, args=(instruction, apply, root), daemon=True).start()

    def stop(self, job_id):
        with self.condition:
            if not self.active or job_id != self.job_id:
                raise ValueError('This demo is no longer running.')
            self.cancel.set()
            self.status = 'stopping'
            self.pending = None
            model = self.model
            self.condition.notify_all()
        if model is not None:
            threading.Thread(target=lambda: self._close_model(model), daemon=True).start()

    @staticmethod
    def _close_model(model):
        try:
            if getattr(model, 'client', None):
                model.client.close()
        except Exception:
            pass

    def ask(self, kind, payload):
        with self.condition:
            if self.cancel.is_set():
                raise RunCancelled('Stopped by the operator.')
            request_id = uuid.uuid4().hex
            self.pending = dict(id=request_id, kind=kind, payload=payload)
            self.answer = None
            self.condition.notify_all()
            answered = self.condition.wait_for(lambda: self.answer is not None or self.cancel.is_set(), timeout=600)
            if self.cancel.is_set():
                self.pending = None
                self.answer = None
                raise RunCancelled('Stopped by the operator.')
            value = self.answer if answered else (False if kind == 'confirmation' else '')
            self.pending = None
            self.answer = None
            return value

    def respond(self, request_id, value):
        with self.condition:
            if not self.pending or self.pending['id'] != request_id or self.answer is not None:
                raise ValueError('This request is no longer pending.')
            if self.pending['kind'] == 'confirmation':
                if not isinstance(value, bool):
                    raise ValueError('Confirmation must be true or false.')
            elif not isinstance(value, str):
                raise ValueError('Credential must be text.')
            self.answer = value
            self.condition.notify_all()

    def _run(self, instruction, apply, root):
        model = None
        runtime = None
        before = None
        try:
            if self.model_factory is None:
                from .models import OpenAIModel
                model = OpenAIModel(self.env_file)
            else:
                model = self.model_factory()
            with self.condition:
                self.model = model
            runtime = Runtime(self.workspace, model, requesting_user='alice', apply=apply,
                max_calls=120, max_steps=12,
                cancel_event=self.cancel,
                confirm=lambda plan: self.ask('confirmation', plan),
                authorize=lambda reason, ids: self.ask('authorization', dict(reason=reason, operation_ids=ids)))
            before = {r['path']: digest(self.workspace / r['path']) for r in inventory(self.workspace)}
            runtime.trace.save('source_snapshot.json', {
                str(p.relative_to(Path(__file__).parent)): p.read_text()
                for p in Path(__file__).parent.rglob('*')
                if p.suffix in {'.py', '.md'} and 'tests' not in p.parts
            })
            runtime.trace.save('demo_settings.json', dict(instruction=instruction,
                requesting_user='alice', synthetic_only=True, apply=apply,
                confirmation_source='live_browser_user', authorization_source='live_browser_user'))
            runtime.trace.save('workspace_before.json', self.before_visible)
            with self.condition:
                self.runtime = runtime
                self.status = 'stopping' if self.cancel.is_set() else 'running'
            runtime.run(instruction)
            after = {r['path']: digest(self.workspace / r['path']) for r in inventory(self.workspace)}
            runtime.trace.save('demo_summary.json', dict(status=runtime.state['status'],
                before=before, after=after, model_calls=runtime.calls))
            with self.condition:
                self.status = runtime.state['status']
        except Exception as exc:
            # Never return provider exception text, which may contain private configuration.
            with self.condition:
                self.error = None if self.cancel.is_set() else type(exc).__name__
                self.status = 'stopped' if self.cancel.is_set() else 'failed'
        finally:
            if runtime is not None and before is not None:
                after = {r['path']: digest(self.workspace / r['path']) for r in inventory(self.workspace)}
                runtime.trace.save('demo_summary.json', dict(status=self.status,
                    before=before, after=after, model_calls=runtime.calls))
            if model is not None:
                if self.cancel.is_set():
                    threading.Thread(target=lambda: self._close_model(model), daemon=True).start()
                else:
                    self._close_model(model)
            with self.condition:
                self.active = False
                self.pending = None
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            runtime = self.runtime
            result = dict(active=self.active, status=self.status, error=self.error,
                          pending=self.pending, bundle=None, files=[], job_id=self.job_id)
            workspace = self.workspace
            before_visible = self.before_visible
        if runtime:
            bundle = load_run(runtime.trace.directory)
            # A poll can overlap an append. Publish only complete JSONL records.
            raw = bundle['trace_text']
            bundle['trace_text'] = raw[:raw.rfind('\n') + 1]
            result['bundle'] = bundle
            result['run_directory'] = str(runtime.trace.directory)
        if workspace:
            # Operator file preview deliberately omits Bob's private inventory.
            rows = visible_workspace(workspace)
            result['files'] = [r['path'] for r in rows if r['kind'] == 'file']
            result['workspace'] = workspace_changes(before_visible, rows)
        return result

    def history(self, submitted_root):
        """Fixed submitted scenarios plus runs beneath this demo's own directory."""
        entries = []
        for name, label in SUBMITTED.items():
            path = Path(submitted_root) / name
            if path.is_dir() and not path.is_symlink() and (path / 'trace.jsonl').is_file():
                entries.append(dict(id='submitted:' + name, label=label, source='submitted', path=path))
        paths = list(self.directory.glob('*/workspace/.organizer/runs/*'))
        for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
            if path.is_dir() and not path.is_symlink() and (path / 'trace.jsonl').is_file():
                settings = read_json(path / 'demo_settings.json', {})
                instruction = settings.get('instruction', 'Live session')
                status = read_json(path / 'manifest.json', {}).get('status', 'recording')
                entries.append(dict(id='local:' + path.name, label=instruction[:90] + ' · ' + status + ' · ' + path.name[-6:],
                                    source='local', path=path))
        return entries

    def history_run(self, submitted_root, entry_id):
        entry = next((e for e in self.history(submitted_root) if e['id'] == entry_id), None)
        if entry is None:
            raise ValueError('Unknown saved trace.')
        path = entry['path']
        bundle = load_run(path)
        bundle['trace_text'] = bundle['trace_text'][:bundle['trace_text'].rfind('\n') + 1]
        baseline_available = True
        if entry['source'] == 'submitted':
            before, after = visible_workspace(path / 'before'), visible_workspace(path / 'after')
        else:
            after = visible_workspace(path.parents[2])
            before = read_json(path / 'workspace_before.json')
            if before is None:
                summary = read_json(path / 'demo_summary.json', {})
                baseline_available = 'before' in summary
                before = [dict(path=p, kind='file', sha256=h, size=None)
                          for p, h in summary.get('before', {}).items()
                          if p.startswith(('users/alice/', 'shared/'))]
        workspace = workspace_changes(before if baseline_available else after, after)
        workspace['baseline_available'] = baseline_available
        return dict(bundle=bundle, workspace=workspace,
                    label=entry['label'], source=entry['source'])


class LiveHandler(ViewerHandler):
    def allowed(self):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        return self.headers.get('Host') in hosts and (
            not self.headers.get('Origin') or self.headers['Origin'] in {f'http://{h}' for h in hosts})

    def send_header(self, key, value):
        if key == 'Content-Security-Policy':
            value = value.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        super().send_header(key, value)

    def reply(self, value, status=200, content_type='application/json; charset=utf-8'):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            self.send_error(403)
            return
        route = urlsplit(self.path).path
        if route == '/api/live/status':
            return self.reply(self.server.demo.snapshot())
        if route == '/api/live/setup':
            return self.reply(dict(token=self.server.demo.token, presets=PRESETS,
                                   preset_workspaces=self.server.demo.preset_views))
        if route == '/api/history':
            return self.reply(dict(runs=[{k: v for k, v in e.items() if k != 'path'}
                for e in self.server.demo.history(self.server.submitted_root)]))
        if route == '/api/history/run':
            try:
                entry_id = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
                return self.reply(self.server.demo.history_run(self.server.submitted_root, entry_id))
            except ValueError as exc:
                return self.reply(dict(error=str(exc)), 404)
        if route == '/':
            return self.reply((ASSETS / 'live.html').read_bytes(), content_type='text/html; charset=utf-8')
        if route in {'/live.js', '/live.css', '/live-feed.js'}:
            return self.reply((ASSETS / route[1:]).read_bytes(), content_type=(
                'text/css; charset=utf-8' if route.endswith('.css') else 'text/javascript; charset=utf-8'))
        if route == '/replay':
            body = (ASSETS / 'index.html').read_text().replace('</head>', '<script defer src="/live-feed.js"></script></head>')
            # Existing assets use paths relative to /replay, resolving at the origin root.
            return self.reply(body.encode(), content_type='text/html; charset=utf-8')
        if route == '/api/bootstrap':
            return self.reply(dict(bundles=[], live=True))
        return super().do_GET()

    def do_POST(self):
        if not self.allowed() or self.headers.get('X-Live-Token') != self.server.demo.token:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('Invalid request size.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected an object.')
            route = urlsplit(self.path).path
            if route == '/api/live/start':
                self.server.demo.start(data.get('instruction'), data.get('preset'), data.get('apply'))
            elif route == '/api/live/stop':
                self.server.demo.stop(data.get('job_id'))
            elif route == '/api/live/respond':
                self.server.demo.respond(data.get('id'), data.get('value'))
            else:
                return self.send_error(404)
            self.reply(dict(ok=True))
        except (ValueError, TypeError) as exc:
            self.reply(dict(error=str(exc)), 400)


def make_server(directory, env_file, port=0, model_factory=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), LiveHandler)
    server.demo = LiveDemo(directory, env_file, model_factory)
    server.submitted_root = Path(__file__).resolve().parents[1] / 'traces' / 'phase1'
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8879)
    parser.add_argument('--directory', type=Path, default=Path.cwd() / '.live-demo')
    parser.add_argument('--env-file', type=Path, default=Path.cwd() / '.env')
    args = parser.parse_args(argv)
    server = make_server(args.directory, args.env_file, args.port)
    print(f'Live demo: http://127.0.0.1:{server.server_port}/\nReal model calls on fresh synthetic workspaces. Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
