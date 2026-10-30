"""Export only the published synthetic traces for static Vercel hosting.

No local sessions, credentials, live execution endpoint, or model calls.
"""
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from downloads_organizer.viewer import ASSETS
from file_organizer.live_demo import LiveDemo, SUBMITTED


def build(destination):
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Rebuild only the generated directory controlled by this script.
    for child in output.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    def save(relative, value):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')
    for name in ['style.css', 'network.css', 'chat.css', 'trace-core.js', 'network.js',
                 'chat.js', 'app.js', 'live.css', 'live.js', 'live-feed.js']:
        shutil.copyfile(ASSETS / name, output / name)
    html = (ASSETS / 'live.html').read_text()
    html = html.replace('File Organizer · Live demo', 'File Organizer · Recorded agent runs')
    html = html.replace('<script defer src="/live.js">', '<script src="/hosted-config.js"></script><script defer src="/live.js">')
    html = html.replace('<form id="run-form">', '<form id="run-form" hidden>')
    html = html.replace('Run a live demo or explore the saved traces published to GitHub.',
                        'Explore the recorded multi-agent runs. Saved replay makes no API calls.')
    html = html.replace('Saved replay makes no API calls. Published traces and your local demo sessions are listed separately.',
                        'Choose one of the published traces. Replay makes no API calls or file changes.')
    (output / 'index.html').write_text(html)
    (output / 'hosted-config.js').write_text('window.PUBLIC_REPLAY = true;\n')
    replay = (ASSETS / 'index.html').read_text().replace('</head>', '<script defer src="/live-feed.js"></script></head>')
    (output / 'replay.html').write_text(replay)
    save('data/bootstrap.json', dict(bundles=[], live=True))
    save('data/setup.json', dict(token=None, presets={}, preset_workspaces={}))
    # Construct a read-only catalog without starting a server or creating local presets.
    demo = object.__new__(LiveDemo)
    demo.directory = ROOT / '.public-site-no-local-sessions'
    catalog = []
    for name, label in SUBMITTED.items():
        entry_id = 'submitted:' + name
        catalog.append(dict(id=entry_id, label=label, source='submitted'))
        result = demo.history_run(ROOT / 'traces' / 'phase1', entry_id)
        save('data/runs/' + name + '.json', result)
    save('data/history.json', dict(runs=catalog))
    print(f'Built static public viewer with {len(catalog)} submitted traces: {output}')
    return output


if __name__ == '__main__':
    build(ROOT / 'public-site')
