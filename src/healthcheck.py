from pathlib import Path
import os
import json
import subprocess
import time

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

SELECTED_FILE = OUTPUT_DIR / "selected_channels.json"
REPORT_FILE = OUTPUT_DIR / "healthcheck.json"
OFFLINE_FILE = OUTPUT_DIR / "offline_channels.json"

FFPROBE = os.environ.get('FFPROBE_PATH', 'ffprobe')
TIMEOUT = 5

selected = json.loads(SELECTED_FILE.read_text(encoding='utf-8'))

report = {
    'timestamp': int(time.time()),
    'checked': 0,
    'ok': 0,
    'offline': 0,
    'offline_channels': [],
    'details': {}
}

for channel, data in selected.items():
    best = data.get('best') or {}
    url = best.get('url', '')

    if not url:
        report['offline'] += 1
        report['offline_channels'].append(channel)
        report['details'][channel] = {'status': 'missing_url'}
        continue

    report['checked'] += 1

    try:
        subprocess.run(
            [FFPROBE, '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', url],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=True
        )

        report['ok'] += 1
        report['details'][channel] = {
            'status': 'ok',
            'url': url
        }

    except Exception as exc:
        report['offline'] += 1
        report['offline_channels'].append(channel)
        report['details'][channel] = {
            'status': 'offline',
            'url': url,
            'error': str(exc)
        }

REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
OFFLINE_FILE.write_text(json.dumps(report['offline_channels'], indent=2, ensure_ascii=False), encoding='utf-8')

print(f"healthcheck completed: {report['ok']} ok / {report['offline']} offline")
