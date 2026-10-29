"""List and validate the explicit file set intended for public GitHub publishing.

Writes /tmp/cs239-public-paths.txt for git add --pathspec-from-file. Scans selected
files and archive members for local configured API keys without printing values.
Does not stage, commit, or publish anything.
"""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import tarfile
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
paths=set()
for name in ['.env.example','.gitignore','LICENSE','README.md','requirements.md','policies.md','pyproject.toml',
             'docs/phase1_boundaries.md','experiments/file_organizer_demo.py','experiments/phase1_traces.py',
             'submission/README.md','submission/STATUS.md','submission/guidelines.md','submission/build.py','submission/manifest.json']:
 paths.add(Path(name))
for directory in ['file_organizer','downloads_organizer','tests','scripts','traces','submission/content','submission/archives']:
 for p in (ROOT/directory).rglob('*'):
  if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store':paths.add(p.relative_to(ROOT))
keys=[v.encode() for k,v in dotenv_values(ROOT/'.env').items() if k.endswith('_API_KEY') and v]
manifest={}
for relative in sorted(paths):
 p=ROOT/relative
 if p.is_symlink() or not p.is_file():raise ValueError(f'Not a regular file: {relative}')
 if p.name=='.env' or (p.name.startswith('.env.') and p.name!='.env.example'):raise ValueError('Private config selected')
 raw=p.read_bytes()
 if any(k in raw for k in keys):raise ValueError(f'Configured key found: {relative}')
 if p.name.endswith('.tar.gz'):
  with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
   for member in archive.getmembers():
    if member.isfile():
     if member.name.endswith('/.env'):raise ValueError('Private env inside archive')
     if any(k in archive.extractfile(member).read() for k in keys):raise ValueError('Configured key inside archive')
 manifest[str(relative)]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
Path('/tmp/cs239-public-paths.txt').write_text('\n'.join(str(p) for p in sorted(paths))+'\n')
Path('/tmp/cs239-public-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(f'Validated {len(paths)} selected public files ({sum(v["bytes"] for v in manifest.values())} bytes).')
print('Configured API keys absent, including archive contents. Private workspaces, unrelated course files, and old private-run reports excluded.')
