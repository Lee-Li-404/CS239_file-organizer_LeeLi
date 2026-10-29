# Requirements — Multi-user File Organizer

Phase 1 prototype · Lee Li · UID 006997204

The application organizes a workspace containing private folders at
`users/<username>/` and a shared folder at `shared/`. Five LLM agents communicate
through explicit queued messages and maintain separate histories: Interpreter,
Planner, Analyzer, Arbiter, and Executor. The application supports organization
requests, evidence-based classification, proposed moves, boundary review,
authorization, user confirmation, and local replay of agent communication.

## Functional requirements

| ID | Required behavior |
| --- | --- |
| R1 | Accept a workspace, requesting-user identity, natural-language instruction, preview/execution mode, and model-call budget. |
| R2 | Interpreter translates user intent; Planner coordinates; Executor gathers filesystem evidence and moves files; Analyzer classifies relayed evidence; Arbiter judges scope boundaries. |
| R3 | Classify documents into Papers, Courses, Receipts, Sensitive, or Other using actual requested metadata and bounded anonymized content when needed. |
| R4 | Planner relays evidence into each agent's appropriate context; unresolved trace IDs do not replace the evidence another agent needs. |
| R5 | Register source/destination/reason/evidence references in versioned plans with content hashes, a digest, and pending-operation status. |
| R6 | Expose explicit confirmation and synthetic authorization capabilities; record responses and operation-bound tokens without logging entered passwords. |
| R7 | Support a cross-boundary sharing decision through the LLM Arbiter, followed by the required authorization and confirmation workflow. |
| R8 | Preserve original contents and every copy; handle conflicts without deletion or overwriting and verify moves with hashes. |
| R9 | Record full visible model requests/responses, actions, inter-agent messages, tool observations, plan state, and a terminal outcome in JSONL. |
| R10 | Provide a local browser viewer with group chat, individual agent histories, abbreviated tool calls, exact arguments/results, and trace/usage inspection. |
| R11 | Provide reproducible synthetic scenarios, offline tests, and at least three real-model Phase 1 episode traces with scenario/policy annotations. |
| R12 | Retain resource controls such as command timeout, output size, and model-call budget, with no application RPM cap for OpenAI. |

## What the application must never do

These are intended requirements; violations remain observable in Phase 1.
The detailed per-agent permission, capability, ordering, information-flow, and
isolation boundaries are in [policies.md](policies.md). The runtime's canonical
policy source is [file_organizer/policy.md](file_organizer/policy.md).

- Delete files, overwrite an occupied destination, or lose any original copy.
- Modify another user's files or use their private evidence to plan this user's work.
- Send other-user evidence to Interpreter/Analyzer, or filenames/content to Arbiter.
- Treat file-contained instructions or approval claims as authority.
- Upload original content when anonymizer output is required, or disclose credentials.
- Execute in preview, with a stale/unconfirmed plan, or without required approval and operation-bound authorization.
- Continue a denied operation, exceed the user's task constraints after approval, or invent evidence/completion.
- Present a failed, incomplete, or policy-violating run as compliant success.

## Prototype scope and limitations

There is no safety-enforcing framework. All five agents actually receive the same
nine tools, and code does not enforce business-policy permissions, routing, scope,
plan approval, or command admission. Policy compliance relies on prompts. Input
validation, parsing limits, timeouts, trace persistence, and explicitly requested
credential checks are ordinary tool mechanics. The command tool runs with the
host account's permissions and is not an OS sandbox.

The local anonymizer supports bounded PDF and UTF-8 text/code excerpts with
heuristic identifier replacement; complete PII detection and OCR are not claimed.
Real model execution requires a configured hosted API; tests and trace replay
are offline. Examples use disposable synthetic data. Harness confirmations are
explicitly labeled and do not represent human semantic review. Traces contain
available assistant output and decision rationale; hidden reasoning is neither
available nor fabricated. See [traces/README.md](traces/README.md) for observed
outcomes, which may include violations even when the runtime finishes normally.
