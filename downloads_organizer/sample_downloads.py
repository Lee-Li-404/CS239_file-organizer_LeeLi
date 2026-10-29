"""Reproducible stratified sample of actual Downloads, copied without mutation.

Only filename-filtered academic PDFs, code, a networking note and a citation
are eligible. This is intentionally not a uniform sample of all private files.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .runtime import digest


def sample_downloads(destination: Path, count: int = 20, seed: int = 239) -> dict:
    if not 15 <= count <= 40:
        raise ValueError("Choose between 15 and 40 sample files.")
    source = Path.home() / "Downloads"
    if not source.is_dir():
        raise ValueError("Downloads directory does not exist.")
    if destination.exists():
        raise ValueError("Use a new destination; existing samples are never overwritten.")
    files = sorted((f for f in source.iterdir() if f.is_file() and not f.is_symlink()),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    academic = re.compile(r"lecture|syllabus|the_book_of_agents|swe.agent|wang24h|nlm_as_attention", re.I)
    private = re.compile(r"passport|visa|offer|medical|mychart|idcard|waiver|transcript|"
                         r"registration|resume|insurance|ticket|recovery|backup.codes", re.I)
    pdfs = [f for f in files if f.suffix.lower() == ".pdf" and academic.search(f.name)
            and not private.search(f.name) and f.stat().st_size <= 32_000_000][:60]
    codes = [f for f in files if f.suffix.lower() == ".py" and
             re.search(r"260d|astar|heuristic[12]", f.name, re.I) and f.stat().st_size <= 2_000_000][:12]
    notes = [f for f in files if f.suffix.lower() == ".md" and "计算机网络" in f.name]
    citations = [f for f in files if f.suffix.lower() == ".bib" and f.stat().st_size <= 2_000_000]
    if len(codes) < 4 or not notes or not citations or len(pdfs) < count - 6:
        raise ValueError("Not enough eligible files for the requested stratified sample.")
    rng = random.Random(seed)
    grouped = defaultdict(list)
    for path in pdfs:
        grouped[digest(path)].append(path)
    pairs = [values for values in grouped.values() if len(values) >= 2]
    selected_pdf = []
    for group in rng.sample(pairs, min(2, len(pairs))):
        selected_pdf.extend(rng.sample(group, 2))
    remaining = [f for f in pdfs if f not in selected_pdf]
    selected_pdf.extend(rng.sample(remaining, count - 6 - len(selected_pdf)))
    selected = selected_pdf + rng.sample(codes, 4) + rng.sample(notes, 1) + rng.sample(citations, 1)
    destination.mkdir(parents=True)
    destination.chmod(0o700)
    before, after = destination / "before", destination / "after"
    before.mkdir()
    after.mkdir()
    rows = []
    for path in sorted(selected):
        sha = digest(path)
        stat = path.stat()
        shutil.copy2(path, before / path.name)
        shutil.copy2(path, after / path.name)
        if not (digest(before / path.name) == digest(after / path.name) == digest(path) == sha):
            raise ValueError("A source changed during copying; do not run this sample.")
        rows.append({"source_name": path.name, "bytes": stat.st_size, "sha256": sha,
                     "source_mtime_ns": stat.st_mtime_ns, "extension": path.suffix.lower()})
    manifest = {"source_directory": str(source), "count": len(rows), "seed": seed,
                "created_at": datetime.now(timezone.utc).isoformat(), "files": rows,
                "sampling": "filename-filtered academic PDFs + code + networking note + citation; "
                            "two real duplicate groups when available; not a uniform sample of Downloads",
                "originals_modified": False}
    (destination / "sample_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=239)
    args = parser.parse_args()
    result = sample_downloads(args.destination.resolve(), args.count, args.seed)
    print(json.dumps({"count": result["count"], "files": [r["source_name"] for r in result["files"]],
                      "sampling": result["sampling"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
