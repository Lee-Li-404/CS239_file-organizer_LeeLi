#!/usr/bin/env python3
"""Build course archives using explicit source/evidence selections; no API calls."""
from __future__ import annotations
import gzip
import hashlib
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'submission'
CONTENT = OUT / 'content'
APP = 'file-organizer'
IDENTITY = dict(name='Lee Li', filename_name='Lee-Li', uid='006997204', appName=APP)
DEMO = ROOT / 'demo/workspaces/file-organizer-d4da8a10'
RUN = DEMO / 'after/.organizer/runs/run_c058abaea9d34acb'
PUBLIC_DEMO = ROOT / 'traces/historical/2026-10-29-demo'
if PUBLIC_DEMO.exists():
    DEMO = PUBLIC_DEMO
    RUN = DEMO / 'run'
SUFFIXES = {'.py', '.md', '.swift', '.js', '.css', '.html', '.json'}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def copy(source, target):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f'Expected regular file: {source}')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive(staging, filename):
    with filename.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w') as tar:
            for path in sorted(staging.rglob('*')):
                if not path.is_file():
                    continue
                info = tar.gettarinfo(str(path), arcname=path.relative_to(staging).as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                info.mode = 0o644
                with path.open('rb') as stream:
                    tar.addfile(info, stream)


def main():
    sys.path.insert(0, str(ROOT))
    from file_organizer.runtime import ROLES
    from file_organizer.tools import agent_tools
    if set(ROLES) != {'interpreter', 'planner', 'analyzer', 'arbiter', 'executor'}:
        raise ValueError('Expected five LLM agents.')
    requirements, whole = OUT / 'requirements', OUT / 'whole'
    for generated in (requirements, whole):
        if generated.exists():
            shutil.rmtree(generated)
        generated.mkdir()
    app = requirements / APP
    for name in ('app.md', 'tools.md'):
        copy(CONTENT / name, app / name)
    policy = (ROOT / 'file_organizer/policy.md').read_text()
    # Fold P3 continuation into its paragraph without changing wording.
    exported_policy = policy.replace("\n\nArbiter's decision rules:", "\nArbiter's decision rules:")
    numbers = re.findall(r'^P(\d+)\.', exported_policy, re.M)
    if len(numbers) < 3 or numbers != [str(i) for i in range(1, len(numbers) + 1)]:
        raise ValueError('Expected at least three consecutively numbered policies.')
    write(app / 'policy.md', exported_policy)
    tools = [t['function']['name'] for t in agent_tools('planner')]
    documented = [line.split(':', 1)[0] for line in (CONTENT / 'tools.md').read_text().splitlines() if line.strip()]
    if set(tools) != set(documented) or len(tools) != len(documented):
        raise ValueError('Tool documentation does not match callable schemas.')
    prompts = ROOT / 'file_organizer/prompts'
    common = (prompts / 'common.md').read_text()
    for role in ROLES:
        actual = common + '\n' + policy + '\n' + (prompts / f'{role}.md').read_text()
        write(app / 'agents' / f'{role}.md', f'# {role.title()}\n\n'
              f'Agent name: {role}. Implementation: LLM-based, independent history.\n\n'
              f'Actually available tools: {", ".join(tools)}.\n\n'
              'P1 defines intended permissions; all nine actual capabilities above are shared.\n'
              'Runtime queues messages without policy filtering.\n\n'
              'Actual system prompt verbatim (common + newline + policy + newline + role):\n\n'
              f'~~~~text\n{actual}\n~~~~\n')
    episodes = sorted((CONTENT / 'episodes').glob('*.md'))
    for episode in episodes:
        copy(episode, app / 'episodes' / episode.name)
    src, tests = whole / 'src', whole / 'tests'
    copy(CONTENT / 'source-README.md', src / 'README.md')
    for name in ('pyproject.toml', '.env.example'):
        copy(ROOT / name, src / name)
    for package in ('file_organizer', 'downloads_organizer'):
        for path in sorted((ROOT / package).rglob('*')):
            if path.is_file() and path.suffix in SUFFIXES and not any(
                part in {'tests', '__pycache__'} for part in path.relative_to(ROOT / package).parts
            ):
                copy(path, src / path.relative_to(ROOT))
    for name in ('file_organizer_demo.py', 'phase1_traces.py', 'deletion_wrong_password.py'):
        copy(ROOT / 'experiments' / name, src / 'experiments' / name)
    for name in ('requirements.md', 'policies.md', 'LICENSE'):
        copy(ROOT / name, src / name)
    for path in sorted((ROOT / 'traces').rglob('*')):
        if path.is_file() and path.suffix in {'.json', '.jsonl', '.md', '.txt'}:
            copy(path, src / path.relative_to(ROOT))
    copy(ROOT / 'traces/README.md', src / 'traces/README.md')
    copy(CONTENT / 'tests-README.md', tests / 'README.md')
    for path in sorted((ROOT / 'tests').glob('test_*')):
        if path.suffix in {'.py', '.cjs'}:
            copy(path, tests / path.name)
    for path in sorted((ROOT / 'file_organizer/tests').glob('test_*.py')):
        copy(path, tests / 'file_organizer' / path.name)
    write(tests / 'run_tests.py', '''"""Run offline suites against this archive's src directory."""
import os
from pathlib import Path
import subprocess
import sys
root = Path(__file__).resolve().parents[1]
env = dict(os.environ, PYTHONPATH=str(root / 'src'))
for suite in (root / 'tests/file_organizer', root / 'tests'):
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(suite), '-v'], cwd=root / 'src', env=env)
    if result.returncode:
        raise SystemExit(result.returncode)
''')
    evidence = src / 'evidence/observed-demo'
    provenance = dict(run_id=RUN.name, historical=True, transformation='Replace absolute project root with $PROJECT_ROOT in exported evidence only; no events omitted.', files={})
    if (DEMO / 'provenance.json').exists():
        provenance['upstream_provenance'] = json.loads((DEMO / 'provenance.json').read_text())
    selected = [(RUN / name, Path('run') / name) for name in (
        'trace.jsonl', 'manifest.json', 'demo_settings.json', 'demo_summary.json', 'source_snapshot.json')]
    selected.append((DEMO / 'summary.json', Path('summary.json')))
    for snapshot in ('before', 'after'):
        for path in sorted((DEMO / snapshot).rglob('*')):
            if path.is_file() and not any(p.startswith('.') for p in path.relative_to(DEMO / snapshot).parts):
                selected.append((path, Path(snapshot) / path.relative_to(DEMO / snapshot)))
    for original, relative in selected:
        if original.is_symlink():
            raise ValueError('Evidence symlink not allowed.')
        raw = original.read_bytes()
        exported = raw.replace(str(ROOT).encode(), b'$PROJECT_ROOT')
        target = evidence / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(exported)
        provenance['files'][relative.as_posix()] = dict(original_sha256=sha(raw), exported_sha256=sha(exported), path_rewritten=raw != exported)
    write(evidence / 'provenance.json', json.dumps(provenance, indent=2) + '\n')
    copy(CONTENT / 'episodes/02-observed-demo.md', evidence / 'episode.md')
    copy(OUT / 'STATUS.md', src / 'SUBMISSION_STATUS.md')
    # Compare configured key values privately; never print them.
    from dotenv import dotenv_values
    keys = [v.encode() for k, v in dotenv_values(ROOT / '.env').items() if k.endswith('_API_KEY') and v]
    for directory in (requirements, whole):
        for path in directory.rglob('*'):
            if path.is_symlink():
                raise ValueError('Symlink in staging.')
            if not path.is_file():
                continue
            if path.name == '.env' or (path.name.startswith('.env.') and path.name != '.env.example'):
                raise ValueError('Private configuration in staging.')
            if any(key in path.read_bytes() for key in keys):
                raise ValueError(f'Configured API key found in {path.relative_to(OUT)}')
    archives = OUT / 'archives'
    archives.mkdir(exist_ok=True)
    names = []
    for label, stage in (('requirements', requirements), ('whole', whole)):
        name = f"{IDENTITY['filename_name']}-{APP}-{IDENTITY['uid']}-{label}.tar.gz"
        archive(stage, archives / name)
        names.append(dict(filename=name, sha256=sha((archives / name).read_bytes()), bytes=(archives / name).stat().st_size))
    write(OUT / 'manifest.json', json.dumps(dict(**IDENTITY, archives=names, configured_api_keys_absent=True), indent=2) + '\n')
    for item in names:
        print(f"Built {item['filename']} ({item['bytes']} bytes)")
    print(f'Validated: five actual prompts, nine tools, {len(numbers)} policies, {len(episodes)} episodes; configured keys excluded.')


if __name__ == '__main__':
    main()
