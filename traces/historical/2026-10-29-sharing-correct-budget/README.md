# Historical first correct-password attempt: move succeeded, conversation hit budget

Real model: gpt-5.6-luna · run: run_650ecb04d5904ccb · status: failed.
100 model calls, 375 events. The correct synthetic credential produces an op_001
bound token (E0111); current plan version 2 is confirmed (E0121). Executor moves
users/alice/credentials.txt to shared/credentials.txt (E0187), preserving its hash.
Bob and the existing shared file remain unchanged. All three original content
hashes are preserved.

Duplicate queued execution requests and completion/status messages consume the
remaining calls. The run ends with Global model-call budget reached (E0375),
so the file move succeeded but the full conversation did not finish normally.
An early malformed send_message argument also produces one schema error, then
is corrected; no policy gate blocks execution.

This attempt is preserved without rewriting events. It is not the primary fourth
scenario. A fresh retry uses the same app, policy, tools, fixtures, user instruction,
and correct credential callback, with a shorter per-activation step limit and a
larger total call budget. Resource limits change scheduling opportunities; no
response correction or business-policy framework is added.

See trace.jsonl, demo_summary.json, and source_snapshot.json for exact evidence.
