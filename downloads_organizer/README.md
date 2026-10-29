# Local anonymizer tool prototype

The Agent explicitly calls `anonymize_file(path)`, receives `artifact_id` and
`output_file`, and selects that output when requesting an upload. No automatic
model-request rewriting or external upload is implemented here.

```python
from downloads_organizer.anonymizer import LocalAnonymizer, TOOL_SCHEMA

# Keep this object in the runtime; expose anonymize_file and TOOL_SCHEMA.
tool = LocalAnonymizer("demo/inbox", known_names=["Alice Example"])
result = tool.anonymize_file("notes.txt")
# Feed result to the Agent. It should choose result["output_file"] for upload.
```

CLI, from the project root:

```sh
python3 -m downloads_organizer.anonymizer notes.txt --workspace demo/inbox
```

Local name lists can be loaded with `--names-file path/to/names.txt`. They are
never sent to a model. Labeled names are also discovered locally, and their
placeholders remain consistent during the lifetime of one Python instance.

Supported input: PDF (at most 32 MB) and UTF-8 `.txt`, `.md`, `.log`, `.py`,
`.bib`, `.tex`, `.csv` (at most 2 MB). PDF extraction runs locally with pypdf;
encrypted PDFs and scanned documents needing OCR produce no artifact. The
default excerpt is at most 6000 characters and two PDF pages. The agent can
request 512–12000 characters and 1–5 pages. Code is read as text, never executed.
The original stays unchanged. Output uses a random neutral filename under `.anonymized/`.
The return value does not include original text, names, secrets, or the source
filename. No mapping is written to disk; name mappings remain in local memory.

Detection covers explicit English/Chinese name and credential fields,
configured exact names, inline English credential assignments, email addresses,
and `sk-`-prefixed keys of the configured pattern. An optional trusted executable
configured through `LOCAL_NAME_DETECTOR` receives text locally and returns names;
the supplied macOS `native_names.swift` helper uses system NaturalLanguage.
No names or mappings from the detector are returned to the agent. It can miss
names or produce false positives. The tool cannot detect every unlabeled
name, password, provider key format, encoded secret, or language. It does not
guarantee that an entire document was inspected. An output artifact proves this tool processed bytes;
it does not prove those bytes contain no remaining personal data.

Phase 1: prompt requires the Agent to call this tool and upload its output.
Phase 2: the course framework should reject uploads of originals, unknown
artifacts or modified output. `resolve_output` offers an in-memory registry
and digest check as a building block, not a completed or race-proof upload guard.
The Phase 1 runtime now includes an upload_file selector: its selected text is
included in the next model request as a tool result, without a separate Files
API call. This standalone anonymizer still never uploads. Framework integration
is pending. Default to synthetic fixtures; real-file tests require explicit
authorization for filenames and anonymized excerpts to the model service,
disposable copies, and private local records. Copying is not an OS sandbox.

Run focused checks:

```sh
python3 -m unittest discover -s tests -p 'test_anonymizer.py' -v
```
