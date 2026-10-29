import json
import tempfile
import unittest
from pathlib import Path

from downloads_organizer.demo import ScriptedDemoModel, create_fixture
from downloads_organizer.runtime import Runtime, digest, inventory
from downloads_organizer.tools import run_command


class CommandDescriptionTests(unittest.TestCase):
    def test_missing_description_never_reaches_subprocess(self):
        class Model:
            model, is_real_model = "invalid-command-test", False
            calls = 0

            def complete(self, agent, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return {"message": {"role": "assistant", "tool_calls": [{
                        "id": "invalid", "type": "function", "function": {
                            "name": "run_command", "arguments": json.dumps({"argv": ["touch", "must_not_exist"]})}}]}}
                return {"message": {"role": "assistant", "content": "Cannot proceed."}}

        with tempfile.TemporaryDirectory() as tmp:
            root = create_fixture(Path(tmp)/"files")
            runtime = Runtime(root, Model())
            runtime.run("test")
            self.assertFalse((root/"must_not_exist").exists())
            events = [json.loads(line) for line in (runtime.trace.directory/"trace.jsonl").read_text().splitlines()]
            errors = [e['result'] for e in events if e['kind']=='tool_result']
            self.assertEqual(errors[0]['error'], 'missing_tool_arguments')
            self.assertIn('description', errors[0]['missing'])

    def test_description_is_data_not_a_shell_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=run_command(root, ["test", "-d", "."], "Check this folder; $(touch injected) is only descriptive text.")
            self.assertEqual(result['exit_code'], 0)
            self.assertFalse((root/'injected').exists())
            with self.assertRaises(ValueError):
                run_command(root, ["touch", "must_not_exist"], " \t")
            self.assertFalse((root/'must_not_exist').exists())

    def test_conflict_trace_preserves_target_and_records_each_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=create_fixture(Path(tmp)/"files", 'conflict')
            original=digest(root/'Courses/CS239/lecture3.txt')
            runtime=Runtime(root, ScriptedDemoModel(), apply=True, confirm=lambda plan: True,
                            max_calls=180, max_steps=100)
            state=runtime.run('Organize synthetic files and preserve conflicts.')
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(digest(root/'Courses/CS239/lecture3.txt'), original)
            events=[json.loads(line) for line in (runtime.trace.directory/'trace.jsonl').read_text().splitlines()]
            commands=[json.loads(e['arguments']) for e in events if e['kind']=='tool_request' and e['name']=='run_command']
            self.assertTrue(commands)
            self.assertTrue(all(a['description'].strip() for a in commands))
            self.assertGreaterEqual(len(state['plans']), 2)

    def test_hard_fixture_has_collisions_revisions_and_thirty_preserved_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=create_fixture(Path(tmp)/'hard', 'hard')
            self.assertEqual(len(inventory(root)), 30)
            self.assertNotEqual(digest(root/'document_001.md'), digest(root/'Papers/document_001.md'))
            self.assertEqual(digest(root/'document_001.md'), digest(root/'document_001_copy.md'))
            self.assertNotEqual(digest(root/'nested/vendor_a/receipt.txt'), digest(root/'nested/vendor_b/receipt.txt'))
            self.assertTrue((root/'Receipts/receipt_october_2.txt').is_file())
            self.assertEqual((root/'empty_document.txt').stat().st_size, 0)
