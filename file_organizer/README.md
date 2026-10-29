# File organizer — policy-only prototype

`policy.md` is the single source of behavioral rules. Runtime loads it verbatim
into each role's system prompt alongside task and tool-protocol descriptions.
Interpreter, Planner, Analyzer, Arbiter, and Executor are model roles with separate
histories and the same tool capabilities. Arbiter makes model-based decisions.

Runtime does not filter inventory by user, redact inter-agent messages, restrict
tools by role, enforce workspace boundaries, or require a plan, confirmation,
Arbiter approval, or authorization before commands. Following those requirements
is the agents' responsibility. Preview status is also advisory to the agents.

`submit_plan` records a plan and its metadata. Confirmation happens only when an
agent calls `request_confirmation`. `request_authorization` checks the existing
synthetic credential (`correct`) and returns an operation-bound token; the result
does not gate subsequent actions. Anonymization happens only when requested.
Format validation, file-parser limits, command timeouts, and model-call budgets
remain tool mechanics. Traces record actions without automatically labeling
policy violations.

From the repository root:

```sh
python -m file_organizer fixture /tmp/organizer-workspace --scenario normal
python -m file_organizer organize /tmp/organizer-workspace --user alice
python -m file_organizer organize /tmp/organizer-workspace --user alice --apply
python -m unittest discover -s file_organizer/tests -v
```

Run a real OpenAI demo with fresh synthetic files, before/after snapshots, and
recorded harness-supplied confirmation and credentials:

```sh
python experiments/file_organizer_demo.py
```

Results are saved under `demo/workspaces/file-organizer-*/`. OpenAI requests have
no application-level RPM cap; the configured model and key come from `.env`.

Inspect a saved run's communication graph with the local web viewer:

```sh
python -m downloads_organizer view --run PATH_TO_RUN_DIRECTORY
```

The default Agent chats view shows an inbox with one row per agent, latest-message
previews, and timestamps. Open an agent to read its sent and received messages.
The pinned Group chat combines every agent's messages in recorded order. Compact
tool calls appear inline by default and can be hidden with Show tool calls.
Click a compact call to inspect its original arguments and matched result;
successful message delivery is shown once as its message, not duplicated as a tool.
Choose a teammate to filter their two-way conversation. The Trace tab shows that
agent's model inputs, responses, tool calls, and results. Click a message or trace
record to inspect its exact saved payload and related records. Narrow windows use
an inbox-to-thread layout with an Agents back button; wider windows show both panes.

The workspace-view selector retains Agent network, Tools & messages, Full event
log, Token usage, and Plans & files. Agent network includes directed handoffs,
per-agent usage, and message replay.
Import `demo_settings.json` or `demo_summary.json` with the trace to display its
recorded call budget. New file-organizer manifests also record the global call
budget and per-activation step limit. Missing budget data is labeled as unknown.
Replay reads saved data and makes no model calls or filesystem changes.

Offline tests exercise both the optional authorization service and deliberate
policy violations against disposable fixtures. They require no model API calls.
