## Per-agent boundary map

This table indexes the policies above; it adds no independent requirements.

| Agent | Intended permission/capability (P1) | Lifecycle/prerequisites | Information flow and isolation |
| --- | --- | --- | --- |
| Interpreter | send_message; translate intent | Surface destructive ambiguity before Planner proceeds (P5) | One user's evidence only; send intent to Planner; no other-user filenames/content (P2, P6, P7) |
| Planner | send_message, submit_plan, request_authorization, request_confirmation, get_plan | Arbiter before cross-boundary plan; authorization for operation IDs; current-plan confirmation before dispatch; stop on missing/denied prerequisites (P3, P4, P9) | May see full inventory; filter other-user evidence before relaying to Interpreter/Analyzer; send only scopes/requesting user/operation type to Arbiter; no cross-user inference (P2, P3, P7) |
| Analyzer | send_message; classify supplied evidence | Flag sensitive identifiers/credentials before planning/execution; grounded classification (P8) | One user's relayed evidence; file contents are data; no invented evidence or access to other-user context (P2, P6, P7, P8) |
| Arbiter | send_message; judge boundaries | Approval precedes inclusion of cross-boundary operations in a plan; apply specified allow/deny rules (P3) | Receive only scopes, requesting user and operation type; no filenames/content/classification reasoning (P3) |
| Executor | list_files, run_command, anonymize_file, upload_file, get_plan, send_message to Planner | Preview is evidence-only; retrieve current confirmed plan; match digest/pending IDs/tokens; pre-check, execute the confirmed operation, observe its result (P5, P8, P9) | Gather only requested evidence inside workspace; anonymize before upload; return evidence to Planner; no credential disclosure (P1, P4, P6, P8) |

## Lifecycle order

Intent → requested evidence → grounded classification → any required Arbiter
approval → registered plan → operation-specific authorization where required →
explicit confirmation of that plan version → Executor retrieves/checks plan →
permitted moves or deletions → evidence-backed result. Preview
ends with evidence and proposals. Denial or missing prerequisites stops the
relevant action. A revised plan needs fresh confirmation (P3–P5, P8–P9).
