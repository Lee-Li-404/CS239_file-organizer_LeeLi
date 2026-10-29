# Test cases

All tests are offline and use temporary synthetic files or mocked model/HTTP
clients. Passing these tests does not prove LLM policy compliance.

Run from the archive root after installing src/ dependencies:

```sh
src/.venv/bin/python tests/run_tests.py
node --test tests/test_trace_core.cjs
```

The runner inserts this archive's src/ in PYTHONPATH and discovers suites
separately to avoid mixing selected-app and older-baseline assumptions.

| Suite | Cases and expected result |
| --- | --- |
| file_organizer/test_policy_only.py | All roles receive identical capabilities and the actual policy; inventory and Arbiter messages remain unfiltered; model judgments are not overridden; confirmation is explicit; disposable commands and raw uploads can bypass business policies; outside/symlink paths are not policy-gated. These are deliberate non-enforcement tests. |
| file_organizer/test_authorization.py | Correct demo credentials issue operation-bound tokens; wrong/empty credentials fail once; token membership is checked; raw passwords are absent from service trace records; preview does not mechanically block a requested credential service. |
| test_tool_protocol.py | Native and JSON-envelope calls, malformed or duplicate IDs, argument validation, compatibility repairs, refusal handling, and no accidental execution of file/prose content. |
| test_models.py | Provider/key configuration, tool-call protocol, pacing for non-OpenAI providers, OpenAI absence of local pacing, and mocked SDK retry behavior. No network is used. |
| test_anonymizer.py | Supported text/PDF extraction, bounded excerpts, consistent replacement of credentials and names, and base-helper artifact validation. Base-helper boundary tests concern the legacy implementation; the selected app overrides its scope check. |
| test_viewer.py; test_trace_core.cjs | Explicit trace/sidecar loading, local HTTP routing, inert hostile strings, event normalization, group chat ordering, compact tool summaries, message/tool pairing, per-agent usage and budget display, and bundled scripted replay provenance. |
| test_runtime.py; test_command_description.py | Older three-agent baseline's scripted flows, preview, conflict/replan, confirmation, undo, evidence round trips, file preservation, command argument mechanics, and failure records. These are compatibility regressions, not evidence of the submitted five-agent model's compliance. |
| test_cost.py | Token accounting and incomplete/unsupported cost handling in the shared reporting helpers. |

The included real demo is assessed manually in the requirements archive's
episodes/02-observed-demo.md. It demonstrates failures rather than serving as a
passing integration test. A fresh successful five-agent live run remains a useful
follow-up evaluation, but the guideline permits hypothetical episodes and does
not require a complete raw execution log.
