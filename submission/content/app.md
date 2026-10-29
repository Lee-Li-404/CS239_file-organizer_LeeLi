# File organizer

Lee Li · UID 006997204

This application organizes a synthetic multi-user workspace with private folders
at `users/<user>/` and a common `shared/` folder. It supports natural-language
organization requests, evidence-based classification into Papers, Courses,
Receipts, Sensitive, and Other, proposed moves and permission-based deletion, user confirmation, optional
authorization, and policy-aware sharing requests. A local viewer shows agent
conversations, group chat, abbreviated tool calls, and exact saved trace records.

Five LLM agents have independent histories: Interpreter translates intent;
Planner coordinates; Executor gathers evidence and performs file operations;
Analyzer classifies evidence; Arbiter judges scope boundaries. Runtime queues
messages and supplies tools. Runtime, the anonymizer, model adapter, and trace
viewer are infrastructure rather than additional decision-making agents.

`policy.md` defines intended behavior. Every agent actually receives all nine
tools; role permissions and approval requirements are prompt instructions. Runtime
does not enforce these business policies. Argument validation, parser limits,
timeouts, credential checking when requested, and model-call budgets are tool
mechanics. This is a policy-assurance research prototype for disposable files.

The selected submission is `file_organizer/`. The `downloads_organizer/` package
contains shared adapters, the viewer, and an older three-agent baseline retained
for compatibility and regression testing. That baseline is not the submitted
five-agent workflow. Example episodes distinguish observed behavior from intended
behavior; the recorded real run is incomplete and contains violations.

Deletion requires explicit permission for the named files, destructive-action
confirmation, and successful Runtime authorization under P4, P5, and P9. P10
was removed; an incorrect password must stop deletion. These remain prompt
requirements, with no code gate on commands.
