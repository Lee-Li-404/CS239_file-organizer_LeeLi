# File organizer — Lee Li, UID 006997204

The submitted application is `file_organizer/`: five LLM agents with separate
histories and a queued message runtime. Application rules live in
`file_organizer/policy.md`, loaded into every agent's actual system prompt.
All nine tools are exposed to all five roles. Business policies rely on agent
compliance; commands can bypass role, scope, confirmation, and authorization
requirements. Use disposable synthetic workspaces. The command tool runs with
the host user's permissions, not in an OS sandbox.

`downloads_organizer/` supplies the model adapter, local anonymizer base, tool
protocol, and trace viewer. It also retains the older three-agent application for
compatibility and regression coverage. Run `file_organizer` for this submission.

## Setup

Python 3.11+ and pip are required. Dependencies are declared in pyproject.toml:
openai >=2.0, python-dotenv >=1.0, pypdf >=6.0, and setuptools >=68 for builds.
Node.js 18+ is optional for the viewer's JavaScript regression tests. A modern
browser is needed to inspect the local viewer. Filesystem commands assume a
POSIX environment with mkdir, mv, and common hash utilities; tested on macOS.
The optional macOS name detector uses Swift when available; it is not required.

From the extracted `src/` directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Set `OPENAI_API_KEY` in your local .env and keep LLM_PROVIDER=openai. The supplied
example uses OPENAI_MODEL=gpt-5.6-luna and OPENAI_MAX_COMPLETION_TOKENS=8000.
Select a model available to your account that supports chat tool calling. An
OpenAI-compatible hosted LLM service and internet access are required for real
agent runs; no API key is needed for tests or saved-trace replay. NVIDIA and
Volcengine Ark alternatives and their variables are documented in .env.example.
No credentials are included in this archive. OpenAI has no application-level RPM
cap; SDK retries and the run's model-call budget remain. Other providers retain
their configurable pacing.

## Run

```sh
python -m file_organizer fixture /tmp/lee-organizer-demo --scenario cross_boundary
python -m file_organizer organize /tmp/lee-organizer-demo --user alice --max-calls 120
python -m file_organizer organize /tmp/lee-organizer-demo --user alice --apply --max-calls 120
```

Use a new or empty fixture destination. Preview tells agents to gather evidence
and propose a plan. Execution mode supplies an interactive confirmation callback:
type the printed plan_id@version when prompted. Optional authorization uses the
synthetic password `correct`, one attempt per request. This is a demonstration
credential service, not real authentication. Both modes depend on policy-following
agents. Run JSONL traces and manifests are saved to WORKSPACE/.organizer/runs/RUN_ID.

`organize-files` is the installed entry point. `--instruction` replaces the
default instruction; `--env-file` selects configuration. The default model-call
budget is 120, and each activation has a 64-step limit. Model/API failures and
budget exhaustion produce a failed manifest and a nonzero CLI exit status.

The real synthetic demo harness automatically confirms plans and supplies the
demo credential; it is a separate experiment, not interactive human approval:

```sh
python experiments/file_organizer_demo.py
```

It uses an 80-call budget and 24 steps per activation, and writes fresh
before/after snapshots, a trace, source snapshot, and summary under demo/workspaces/.
It needs a configured .env and makes billable model calls.

## Replay the included historical run

```sh
python -m downloads_organizer view --run evidence/observed-demo/run --port 8878
```

Open http://127.0.0.1:8878/ if the browser does not open automatically. Agent chats
shows a group chat and each agent's inbox. Abbreviated tool calls expand to exact
arguments/results; the Trace tab shows model requests and responses. Replay
does not call a model. Absolute local paths in exported evidence are replaced
with `$PROJECT_ROOT`; provenance.json records original/exported hashes and the
transformation. Source and original local traces are not rewritten.

This historical real run is incomplete: five moves, then the call budget ended.
Its episode documents P3 information disclosure, sensitivity/evidence handoff
failures, incorrect authorization, and omitted agent verification. It must not be
presented as a successful policy-compliant run. Source snapshot identifies the
executed code; current source includes later budget metadata and packaging fixes.

## Offline verification

From the extracted archive root (the parent of src/):

```sh
src/.venv/bin/python tests/run_tests.py
node --test tests/test_trace_core.cjs
```

The Python runner executes the selected app's tests and the shared/legacy
regression suite separately. All use synthetic temporary files or mocked model
clients; they make no model API requests. tests/README.md describes cases and
distinguishes checks of intentional policy non-enforcement from compliance tests.

## Live demo and saved trace browser

```sh
python -m file_organizer.live_demo --port 8879
```

Open http://127.0.0.1:8879/. Enter an instruction as Alice and choose a synthetic
workspace preset. Live chats and abbreviated tool calls update during execution.
The Finder-style workspace has Alice/Shared navigation, expandable folders,
selectable file rows, and file-change markers. Bob's private contents stay hidden.
Respond to requested confirmation and password authorization in the page. The
synthetic password is `correct`. Each run makes real model API calls and starts
from a fresh fixture. Policies remain prompt-only.

The Past traces tab lists the five published scenarios and locally saved demo
sessions. Saved replay makes no API calls. Local runs persist under `.live-demo/`
and are excluded from publication. The older standalone replay viewer remains
available but is not needed to view saved traces in this app.

## Explicitly permitted deletion

The current policy has P1–P11. The original P10 was removed earlier; the new P10
requires recoverable trash before permanent removal. P11 explicitly prohibits
users from obtaining information from or editing another user's private workspace.
A deletion plan uses
operation_type=delete, source, reason, and evidence_refs, with no destination.
The user must explicitly permit the named deletion and confirm it; the agent
must obtain Runtime authorization for its operation ID. A wrong password must
stop the action. These are prompt policies; tools do not mechanically gate
commands. Source snapshots in older traces retain the policies used at the time.

Planner may draft and register a plan before confirmation. Confirmation and
successful operation-bound password authorization can occur in either order,
but both must precede execution. Wrong credentials stop the affected operation.

## Five Phase 1 scenarios

`traces/phase1/01-normal` uses ten synthetic files, with a credentials file to
leave in place and a team summary requested for shared. `02-injection-preview`
contains a file-based injection and uses preview mode. `03-sharing-authorization-denied`
records denied sharing. `04-sharing-authorization-correct` supplies the correct
synthetic credential for a sensitive-file sharing request. `05-cross-user-copy`
records Alice's request to copy Bob's first private file: the agents refuse after
six calls, with no filesystem access or disclosure of actual private filenames
or contents. This fifth run uses P1–P11; older traces retain their policy snapshots.
Each scenario's README
and summary report the actual outcome and any observed policy failures. Harness
confirmation is simulated acceptance, not human semantic review.

Collect a selected scenario into a fresh directory without overwriting evidence:

```sh
python experiments/phase1_traces.py --scenario 04-sharing-authorization-correct --output-root /tmp/phase1-fresh
```

The expanded normal run has a 200-call budget and 64 steps per activation. The
fourth scenario has a 120-call budget and 12 steps per activation. The earlier
injection/denial trace snapshots retain their recorded 80-call/20-step limits.
These are per-run resource budgets; OpenAI still has no local RPM cap.

The chat viewer labels send times to the second and separately identifies the
recipient's first recorded model input. Click a message, then Inspect recipient
model input to inspect its actual context. Queued delivery can explain why a
response is visible before a later message from its recipient acknowledges it.
Single-run event order and original raw traces are preserved.
