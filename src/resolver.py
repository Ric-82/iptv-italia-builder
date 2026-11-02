from pathlib import Path
import json
import re
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT = BASE_DIR / 'output'
CHANNELS_FILE = OUTPUT / 'canonical_channels.json'
OUT_FILE = OUTPUT / 'resolved_urls.json'
STATS_FILE = OUTPUT / 'resolver_stats.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0'
}


def resolve_mediapolis(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)

        html = r.text

        m = re.search(r'<source\s+src="([^"]+)"', html, flags=re.I)
        if not m:
            return None

        return {
            'resolved_url': m.group(1),
            'resolver': 'mediapolis'
        }

    except Exception:
        return None


channels = json.loads(CHANNELS_FILE.read_text(encoding='utf-8'))
resolved = {}
stats = {
    'urls_seen': 0,
    'resolved_mediapolis': 0,
    'direct': 0,
    'errors': 0
}

for channel, entries in channels.items():
    resolved[channel] = []

    seen = set()

    for entry in entries:
        url = entry.get('url', '')
        if not url or url in seen:
            continue

        seen.add(url)
        stats['urls_seen'] += 1

        item = {
            'playlist': entry.get('playlist'),
            'display_name': entry.get('display_name'),
            'original_url': url,
            'resolved_url': url,
            'status': 'unresolved'
        }

        try:
            if 'mediapolis.rai.it' in url:
                result = resolve_mediapolis(url)
                if result:
                    item['resolved_url'] = result['resolved_url']
                    item['resolver'] = 'mediapolis'
                    item['status'] = 'resolved'
                    stats['resolved_mediapolis'] += 1
                else:
                    item['resolver'] = 'mediapolis'
                    item['status'] = 'failed'
            else:
                item['resolver'] = 'none'
                item['status'] = 'direct'
                stats['direct'] += 1

        except Exception as exc:
            item['status'] = 'error'
            item['error'] = str(exc)
            stats['errors'] += 1

        resolved[channel].append(item)

OUT_FILE.write_text(json.dumps(resolved, indent=2, ensure_ascii=False), encoding='utf-8')
STATS_FILE.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding='utf-8')

print('resolver v2 completed')
