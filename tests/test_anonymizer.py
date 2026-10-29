import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from downloads_organizer.anonymizer import LocalAnonymizer, TOOL_SCHEMA


class LocalAnonymizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = LocalAnonymizer(self.root, known_names=["张三"])

    def test_redacts_labeled_names_credentials_and_repeated_values(self):
        content = (
            "Name: Alice Example\nPassword: DEMO_PASS_123\n"
            "API_KEY: DEMO_KEY_456\n"
            "Alice Example uses DEMO_PASS_123 and DEMO_KEY_456.\n"
            "张三的课程是 CS239。\n"
        )
        source = self.root / "notes.txt"
        source.write_text(content)
        result = self.tool.anonymize_file("notes.txt")
        output = self.tool.resolve_output(result["artifact_id"]).read_text()
        for value in ["Alice Example", "DEMO_PASS_123", "DEMO_KEY_456", "张三"]:
            self.assertNotIn(value, output)
            self.assertNotIn(value, json.dumps(result, ensure_ascii=False))
        self.assertIn("[PERSON_", output)
        self.assertIn("[REDACTED_PASSWORD]", output)
        self.assertIn("[REDACTED_API_KEY]", output)
        self.assertIn("CS239", output)
        self.assertEqual(source.read_text(), content)

    def test_inline_credentials_and_key_pattern(self):
        key = "sk-" + "FAKEONLY1234567890"
        (self.root / "notes.md").write_text(
            f'Example password="demo password"; api_key=demo_token\nKey: {key}\n'
        )
        result = self.tool.anonymize_file("notes.md")
        output = self.tool.resolve_output(result["artifact_id"]).read_text()
        for value in ["demo password", "demo_token", key]:
            self.assertNotIn(value, output)

    def test_chinese_labels_and_consistency_across_files(self):
        for name in ["first.txt", "second.txt"]:
            (self.root / name).write_text("姓名：李四\n密码：测试假密码\n李四的课件\n")
        outputs = []
        for name in ["first.txt", "second.txt"]:
            result = self.tool.anonymize_file(name)
            outputs.append(self.tool.resolve_output(result["artifact_id"]).read_text())
        self.assertEqual(outputs[0], outputs[1])
        self.assertNotIn("李四", outputs[0])
        self.assertNotIn("测试假密码", outputs[0])

    def test_unknown_and_modified_artifacts_are_not_verified(self):
        with self.assertRaises(ValueError):
            self.tool.resolve_output("anon_fake")
        (self.root / "notes.txt").write_text("Name: Alice Example\n")
        result = self.tool.anonymize_file("notes.txt")
        output = self.tool.resolve_output(result["artifact_id"])
        output.write_text("modified")
        with self.assertRaises(ValueError):
            self.tool.resolve_output(result["artifact_id"])

    def test_rejects_outside_source_and_symlink(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "private.txt"
            target.write_text("do not read")
            with self.assertRaises(ValueError):
                self.tool.anonymize_file(str(target))
            (self.root / "link.txt").symlink_to(target)
            with self.assertRaises(ValueError):
                self.tool.anonymize_file("link.txt")

    def test_failure_produces_no_artifact(self):
        (self.root / "bad.txt").write_bytes(b"\xff\xfe")
        (self.root / "unsupported.pdf").write_bytes(b"%PDF-placeholder")
        for name in ["bad.txt", "unsupported.pdf"]:
            with self.assertRaises(ValueError):
                self.tool.anonymize_file(name)
        self.assertFalse((self.root / ".anonymized").exists())

    def test_tool_exposes_only_source_path(self):
        self.assertEqual(TOOL_SCHEMA["parameters"]["required"], ["path"])
        self.assertEqual(set(TOOL_SCHEMA["parameters"]["properties"]), {"path", "max_chars", "max_pages"})

    def test_distinct_names_keep_distinct_placeholders(self):
        tool = LocalAnonymizer(self.root, known_names=[" Alice Example ", "Alice Example"])
        (self.root / "names.txt").write_text("Name: Bob Example\nAlice Example\n")
        result = tool.anonymize_file("names.txt")
        output = tool.resolve_output(result["artifact_id"]).read_text()
        self.assertIn("[PERSON_001]", output)
        self.assertIn("[PERSON_002]", output)

    def test_pdf_is_extracted_locally_and_original_bytes_stay_unchanged(self):
        source = self.root / "paper.pdf"
        original = b"PDF reader is mocked for this extraction-boundary check."
        source.write_bytes(original)
        page = MagicMock()
        page.get_contents.return_value = None
        page.extract_text.return_value = "Name: Alice Example\nAPI_KEY: DEMO_ONLY\nAbstract: agents.\n"
        with patch("pypdf.PdfReader") as reader:
            reader.return_value.is_encrypted = False
            reader.return_value.pages = [page]
            result = self.tool.anonymize_file("paper.pdf")
        output = self.tool.resolve_output(result["artifact_id"]).read_text()
        self.assertNotIn("Alice Example", output)
        self.assertNotIn("DEMO_ONLY", output)
        self.assertIn("Abstract: agents", output)
        self.assertTrue(result["excerpt_only"])
        self.assertEqual(source.read_bytes(), original)

    def test_code_is_read_as_data_and_excerpt_limit_is_respected(self):
        (self.root / "program.py").write_text("raise RuntimeError('must never execute')\n" + "x" * 2000)
        result = self.tool.anonymize_file("program.py", max_chars=512)
        self.assertEqual(len(self.tool.resolve_output(result["artifact_id"]).read_text()), 512)
        self.assertTrue(result["excerpt_only"])
        with self.assertRaises(ValueError):
            self.tool.anonymize_file("program.py", max_pages=999)

    def test_configured_local_detector_names_are_replaced_not_returned(self):
        (self.root / "note.txt").write_text("A meeting with Bob Smith.\n")
        result_object = MagicMock(stdout='["Bob Smith"]')
        with patch.dict("os.environ", {"LOCAL_NAME_DETECTOR": "/local/trusted-detector"}), \
                patch("downloads_organizer.anonymizer.subprocess.run", return_value=result_object):
            result = self.tool.anonymize_file("note.txt")
        output = self.tool.resolve_output(result["artifact_id"]).read_text()
        self.assertNotIn("Bob Smith", output)
        self.assertNotIn("Bob Smith", json.dumps(result))
        self.assertTrue(result["local_name_detector"])


if __name__ == "__main__":
    unittest.main()
