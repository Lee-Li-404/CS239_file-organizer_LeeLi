# Submission coverage and remaining work

Owner: Lee Li · UID 006997204 · application: file-organizer.

The supplied guideline's required materials are included in two archives. This
means the submission structure is complete; the prototype has not demonstrated
reliable policy compliance.

| Guideline item | Included material |
| --- | --- |
| Same name/appName/UID; two .tar.gz files | Lee-Li-file-organizer-006997204-requirements.tar.gz and Lee-Li-file-organizer-006997204-whole.tar.gz |
| Purpose and supported tasks | requirements/file-organizer/app.md |
| Consecutively numbered, separate policy paragraphs | policy.md, P1–P10; P3 continuation folded into its paragraph without changing words |
| Actual callable tools, one per line, inputs/outputs | tools.md, nine names checked against schemas |
| One agent file with actual prompt and available tools | agents/{interpreter,planner,analyzer,arbiter,executor}.md; five exact composed prompts and nine actual capabilities |
| Example episodes | One hypothetical intended workflow; one real partial run with violations and event references |
| Implementation and setup/environment/run instructions | whole/src/README.md, pyproject.toml, .env.example, application/support packages and demo harness |
| Top-level tests describing cases | whole/tests/README.md and runnable offline suites |

## Resolved during preparation

Unfinished rule-based Arbiter edits were removed at the user's request. Arbiter
is again a fifth LLM agent and receives messages unchanged. Business policies
remain prompt instructions from file_organizer/policy.md. Installation now includes
file_organizer, its policy/prompts, and organize-files. New manifests retain budget
metadata. The original implementation and existing viewer commands remain in place.

## Verification

Clean archive extraction passed 21 selected-app Python tests, 86 shared/legacy
Python tests, and 27 viewer JavaScript tests (134 total). A wheel built offline
with --no-deps --no-build-isolation includes the policy, prompts, viewer assets,
and organize-files entry point. All five documented system prompts exactly match
runtime composition. The 318-event historical trace loads without viewer warnings.
The builder checks archive identities, tool names, policy numbering, and exclusion
of configured API keys. Three additional hosted-model episodes were subsequently collected for Phase 1;
all reach run_finished and have complete JSONL, source snapshots, and file audits.

## Remaining limitations and recommended evaluation

1. Three fresh real-model episodes now finish normally: normal organization
   (completed, 57 calls), injection preview (preview_ready, 18 calls), and denied
   sensitive sharing (not_executed, 63 calls). All originals are preserved. Normal
   organization succeeds at the file task but still has policy violations; a fully
   compliant real execution remains unproven. See traces/README.md.
2. The historical real episode contains P3 filename disclosure, missing sensitivity evidence
   delivery, sensitive-file moves against the task, incorrect operation-bound
   authorization, and omitted agent post-move verification. Sharing never ran;
   do not claim an unauthorized sharing transfer. Prompt-level evidence handoff,
   token handling, task-constraint preservation, and coordination need improvement.
   This submission deliberately adds no code policy gates.
3. Authorization uses a known synthetic credential and auto-approved demo
   callbacks. It is not production authentication or human semantic review.
4. Anonymization is bounded and heuristic, without OCR or guaranteed complete
   identifier detection. Tests and examples use synthetic data.
5. Offline tests verify mechanics and intentional policy non-enforcement; they
   cannot establish model compliance. Three live scenarios are included, but larger
   repeated evaluation and broader adversarial coverage remain future work.
6. Replay evidence is historical and includes its executed source snapshot.
   Absolute project paths are normalized only in exported copies, with hash
   provenance. Current source includes later budget metadata/packaging fixes.

The separate Phase 1 brief requires a public GitHub repository, MIT/Apache 2
license, root requirements.md and policies.md, and three complete agentic traces
with traces/README.md. These have now been prepared alongside the two-archive
format. No hosted deployment, video, cost target, or Phase 2 enforcement framework
is added. Public repository: Lee-Li-404/CS239_file-organizer_LeeLi.
