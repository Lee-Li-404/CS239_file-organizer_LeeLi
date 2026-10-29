"""Live UI transport tests without provider calls or policy enforcement gates."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from file_organizer.live_demo import LiveDemo, visible_workspace, workspace_changes


class RefusalModel:
    model = 'offline-test'
    is_real_model = False

    def complete(self, role, history, tools):
        return {'message': {'role': 'assistant', 'content': None, 'refusal': 'Offline test response'}}


class LiveDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.demo = LiveDemo(Path(self.temp.name), Path(self.temp.name) / '.env', RefusalModel)

    def finish(self):
        with self.demo.condition:
            self.assertTrue(self.demo.condition.wait_for(lambda: not self.demo.active, timeout=5))

    def test_fresh_workspaces_and_complete_trace(self):
        self.demo.start('Test instruction', 'normal', False)
        self.finish()
        first = self.demo.workspace
        state = self.demo.snapshot()
        self.assertFalse(state['active'])
        events = [json.loads(x) for x in state['bundle']['trace_text'].splitlines()]
        self.assertEqual(events[0]['kind'], 'run_started')
        self.assertEqual(events[-1]['kind'], 'run_finished')
        self.assertEqual(events[1]['message']['payload']['requesting_user'], 'alice')
        self.assertFalse(any('users/bob/' in p for p in state['files']))
        (first / 'users/alice/notes.txt').unlink()
        self.demo.start('New instruction', 'normal', True)
        self.finish()
        self.assertNotEqual(first, self.demo.workspace)
        self.assertTrue((self.demo.workspace / 'users/alice/notes.txt').exists())

    def test_runtime_waits_for_browser_and_rejects_stale_reply(self):
        result = []
        thread = threading.Thread(target=lambda: result.append(self.demo.ask('confirmation', {'plan_id': 'p'})))
        thread.start()
        with self.demo.condition:
            self.assertTrue(self.demo.condition.wait_for(lambda: self.demo.pending is not None, timeout=2))
            request_id = self.demo.pending['id']
        with self.assertRaises(ValueError):
            self.demo.respond('stale', True)
        self.demo.respond(request_id, False)
        thread.join(2)
        self.assertEqual(result, [False])
        self.assertIsNone(self.demo.pending)

    def test_password_is_not_exposed_in_pending_payload(self):
        result = []
        thread = threading.Thread(target=lambda: result.append(self.demo.ask('authorization', {'reason': 'test'})))
        thread.start()
        with self.demo.condition:
            self.assertTrue(self.demo.condition.wait_for(lambda: self.demo.pending is not None, timeout=2))
            request_id = self.demo.pending['id']
        self.demo.respond(request_id, 'wrong-demo-password')
        thread.join(2)
        self.assertEqual(result, ['wrong-demo-password'])
        self.assertNotIn('wrong-demo-password', json.dumps(self.demo.snapshot()))

    def test_invalid_start_does_not_run(self):
        for args in [('', 'normal', True), ('hi', '../outside', True), ('hi', 'normal', 'yes')]:
            with self.assertRaises(ValueError):
                self.demo.start(*args)
        self.assertFalse(self.demo.active)

    def test_workspace_view_detects_moves_modifications_and_excludes_bob(self):
        self.demo.start('Test', 'normal', False)
        self.finish()
        workspace = self.demo.workspace
        source = workspace / 'users/alice/notes.txt'
        source.rename(workspace / 'shared/notes.txt')
        (workspace / 'users/alice/paper.md').write_text('Changed synthetic paper')
        state = self.demo.snapshot()['workspace']
        changes = {(c['path'], c['change']) for c in state['changes']}
        self.assertIn(('users/alice/notes.txt', 'moved out'), changes)
        self.assertIn(('shared/notes.txt', 'moved in'), changes)
        self.assertIn(('users/alice/paper.md', 'modified'), changes)
        self.assertNotIn('users/bob/', json.dumps(state))
        self.assertNotIn('Changed synthetic paper', json.dumps(state))

    def test_history_is_allowlisted_and_local_sessions_survive_restart(self):
        submitted = Path(self.temp.name) / 'submitted'
        self.demo.start('An earlier live query', 'normal', False)
        self.finish()
        restored = LiveDemo(self.temp.name, Path(self.temp.name) / '.env', RefusalModel)
        history = restored.history(submitted)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['source'], 'local')
        entry = restored.history_run(submitted, history[0]['id'])
        self.assertTrue(entry['workspace']['before'])
        self.assertEqual(entry['workspace']['changes'], [])
        with self.assertRaises(ValueError):
            restored.history_run(submitted, '../../.env')

    def test_preset_preview_has_alice_and_shared_but_no_bob_metadata(self):
        preview = self.demo.preset_views['cross_boundary']
        self.assertIn('users/alice/share_this.txt', json.dumps(preview))
        self.assertNotIn('users/bob/', json.dumps(preview))
        self.assertEqual(preview['changes'], [])

    def test_duplicate_hashes_do_not_fabricate_a_unique_move(self):
        before = [dict(path='users/alice/a', kind='file', sha256='same'),
                  dict(path='users/alice/b', kind='file', sha256='same')]
        after = [dict(path='shared/c', kind='file', sha256='same')]
        changes = workspace_changes(before, after)['changes']
        self.assertFalse(any(c['change'].startswith('moved') for c in changes))
