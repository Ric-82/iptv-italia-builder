from pathlib import Path
import json
import re
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / 'cache' / 'playlists'
OUTPUT_DIR = BASE_DIR / 'output'
OUTPUT_DIR.mkdir(exist_ok=True)

OUT_FILE = OUTPUT_DIR / 'canonical_channels.json'
OUT_STATS = OUTPUT_DIR / 'canonical_stats.json'
EPG_FILE = OUTPUT_DIR / 'epg_sources.json'

ITALIAN_PLAYLISTS = {'DavideFulgione','TOPTiVU','OpenIPTVItaly','IPTVItalia'}
EPG_PLAYLISTS = {'DavideFulgione','TOPTiVU','OpenIPTVItaly'}


def extract_urls(text):
    if not text:
        return []
    return re.findall(r'https?://[^\s"<>]+', text, flags=re.IGNORECASE)


def extract_first_url(text):
    urls = extract_urls(text)
    return urls[0] if urls else ''


def extract_epg_urls(text):
    return list(dict.fromkeys([
        u for u in extract_urls(text)
        if '.xml' in u.lower() or '.gz' in u.lower()
    ]))


def extract_attr(line, name):
    m = re.search(rf'{re.escape(name)}="([^"]*)', line)
    return m.group(1).strip() if m else ''


def canonical_key(tvg_id, tvg_name, display_name):
    key = (tvg_id or tvg_name or display_name).upper()
    key = re.sub(r'\.IT$', '', key)
    key = re.sub(r'[^A-Z0-9]', '', key)
    return key


channels = defaultdict(list)
stats = {}
epg_sources = {}
all_epg = []

for playlist in CACHE_DIR.glob('*.m3u'):
    pname = playlist.stem
    lines = playlist.read_text(encoding='utf-8', errors='ignore').splitlines()
    count = 0

    if pname in EPG_PLAYLISTS and lines:
        header_blob = '\n'.join(lines[:10])
        urls = extract_epg_urls(header_blob)
        if urls:
            epg_sources[pname] = urls
            all_epg.extend(urls)

    for i, line in enumerate(lines):
        line = line.strip()

        if not line.startswith('#EXTINF'):
            continue

        include = pname in ITALIAN_PLAYLISTS or (pname == 'FreeTV' and 'tvg-country="IT"' in line)
        if not include:
            continue

        tvg_id = extract_attr(line, 'tvg-id')
        tvg_name = extract_attr(line, 'tvg-name')
        tvg_chno = extract_attr(line, 'tvg-chno')
        group_title = extract_attr(line, 'group-title')
        display_name = line.rsplit(',', 1)[-1].strip()
        tvg_logo = extract_first_url(line)

        url = ''
        # ignora tutte le righe metadata (#EXTVLCOPT, #KODIPROP, ecc.)
        for j in range(i + 1, min(i + 15, len(lines))):
            candidate_line = lines[j].strip()

            if not candidate_line:
                continue

            if candidate_line.startswith('#'):
                continue

            candidate = extract_first_url(candidate_line)
            if candidate.startswith('http'):
                url = candidate
                break

        key = canonical_key(tvg_id, tvg_name, display_name)

        channels[key].append({
            'playlist': pname,
            'display_name': display_name,
            'tvg_id': tvg_id,
            'tvg_name': tvg_name,
            'tvg_logo': tvg_logo,
            'tvg_chno': tvg_chno,
            'group_title': group_title,
            'url': url
        })
        count += 1

    stats[pname] = count

all_epg = list(dict.fromkeys(all_epg))
preferred = ''
if epg_sources.get('OpenIPTVItaly'):
    preferred = epg_sources['OpenIPTVItaly'][0]
elif all_epg:
    preferred = all_epg[0]

epg_sources['_all'] = all_epg
epg_sources['_preferred'] = preferred

OUT_FILE.write_text(json.dumps(dict(channels), indent=2, ensure_ascii=False), encoding='utf-8')
OUT_STATS.write_text(json.dumps({'channels': len(channels), 'playlist_stats': stats}, indent=2, ensure_ascii=False), encoding='utf-8')
EPG_FILE.write_text(json.dumps(epg_sources, indent=2, ensure_ascii=False), encoding='utf-8')

print('parser v3 completed')
