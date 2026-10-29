"""Export the canonical runtime policy and its per-agent documentation index."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HEADER = '# Phase 1 policies — Multi-user File Organizer\n\nThis document is a publication copy of `file_organizer/policy.md`, the only policy\nsource loaded by the runtime. No safety-enforcing framework is installed. All\nroles actually receive all nine tool capabilities. Permissions below are intended\nrequirements, not code-enforced restrictions. Edit the canonical policy and\nregenerate this copy with `python scripts/sync_policies.py`.\n\n'
policy = (ROOT / 'file_organizer/policy.md').read_text().replace("\n\nArbiter's decision rules:", "\nArbiter's decision rules:")
appendix = (ROOT / 'docs/phase1_boundaries.md').read_text()
(ROOT / 'policies.md').write_text(HEADER + policy + '\n' + appendix)
