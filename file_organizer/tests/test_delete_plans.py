"""Deletion plan representation and observed completion, without policy gates."""
import tempfile
import unittest
from pathlib import Path

from file_organizer.fixtures import create_workspace
from file_organizer.runtime import Runtime
from file_organizer.tools import agent_tools
from downloads_organizer.tool_protocol import arguments_for


class IdleModel:
    model = 'offline-no-actions'
    is_real_model = False

    def complete(self, role, messages, tools):
        return {'message': {'role': 'assistant', 'content': 'Done.'}}


class DeletePlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = create_workspace(Path(temp.name)/'workspace')
        self.runtime = Runtime(self.workspace, IdleModel(), requesting_user='alice',
                               apply=True, confirm=lambda plan: True)

    def propose(self):
        return self.runtime.submit_plan('Delete the named synthetic file', [{
            'operation_type': 'delete', 'source': 'users/alice/notes.txt',
            'reason': 'Explicit test request', 'evidence_refs': [],
        }])

    def test_delete_schema_and_pending_status_do_not_require_destination(self):
        args = {'summary': 'Deletion', 'operations': [{
            'operation_type': 'delete', 'source': 'users/alice/notes.txt',
            'reason': 'Test', 'evidence_refs': [],
        }]}
        parsed, _ = arguments_for('submit_plan', args, agent_tools('planner'))
        registered = self.runtime.submit_plan(**parsed)
        state = self.runtime.get_plan(registered['plan_id'], registered['version'])
        self.assertEqual(state['pending_operation_ids'], ['op_001'])
        self.assertIsNone(state['plan']['operations'][0]['dest_scope'])
        (self.workspace/'users/alice/notes.txt').unlink()
        self.assertEqual(self.runtime.get_plan(registered['plan_id'], registered['version'])['pending_operation_ids'], [])

    def test_legacy_move_still_requires_a_destination(self):
        with self.assertRaisesRegex(ValueError, 'requires a destination'):
            self.runtime.submit_plan('Malformed move', [{
                'source': 'users/alice/notes.txt', 'reason': 'Test', 'evidence_refs': [],
            }])
        self.assertEqual(self.runtime.plans, [])

    def test_confirmed_delete_reports_completed_after_source_removal(self):
        plan = self.propose()
        self.runtime.request_confirmation(plan['plan_id'], plan['version'])
        (self.workspace/'users/alice/notes.txt').unlink()
        result = self.runtime.run('Observe the existing registered plan')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['moves'], [])
