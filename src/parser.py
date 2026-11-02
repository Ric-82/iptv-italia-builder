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
REPORT_FILE = OUTPUT_DIR / 'canonicalization_report.json'

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


TECH_SUFFIX_RE = re.compile(r'^(?P<base>.+?)(?:FHD|UHD|HD|SD|4K|HEVC|H265)+(?:\d{2,3})?$')
KEY_ALIASES = {
    '20MEDIASET': '20',
    '27TWENTYSEVEN': 'TWENTYSEVEN',
}
# "4K" is part of the channel name here, not a quality suffix
PROTECTED_KEYS = {'RAI4K'}


def raw_key(tvg_id, tvg_name, display_name):
    key = (tvg_id or tvg_name or display_name).upper()
    key = re.sub(r'\.IT$', '', key)
    key = re.sub(r'[^A-Z0-9]', '', key)
    return key


def normalize_key(key):
    # removes technical suffixes (HD, FHD, 4K...) and the trailing channel number that follows them
    prev = None
    while key != prev:
        prev = key
        key = KEY_ALIASES.get(key, key)
        if key in PROTECTED_KEYS:
            break
        m = TECH_SUFFIX_RE.match(key)
        if m:
            key = m.group('base')
    return key


def canonical_key(tvg_id, tvg_name, display_name):
    return normalize_key(raw_key(tvg_id, tvg_name, display_name))


channels = defaultdict(list)
raw_keys = set()
aliases = {}
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

        rk = raw_key(tvg_id, tvg_name, display_name)
        key = normalize_key(rk)
        raw_keys.add(rk)
        if key != rk:
            aliases[rk] = key

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

REPORT_FILE.write_text(json.dumps(dict(sorted(aliases.items())), indent=2, ensure_ascii=False), encoding='utf-8')

COLLISION_FILE = OUTPUT_DIR / 'canonical_collision_report.json'
CHNO_CONFLICTS_FILE = OUTPUT_DIR / 'chno_conflicts.json'
CHNO_ANALYSIS_FILE = OUTPUT_DIR / 'chno_analysis.json'


def find_name_collisions(keys):
    # diagnostics only: a key is a candidate alias of a shorter key when the remainder is a number or a 1-2 letter tag
    keyset = set(keys)
    report = defaultdict(list)
    for base in keyset:
        if len(base) < 3:
            continue
        for other in keyset:
            if other != base and other.startswith(base) and re.fullmatch(r'\d{2,4}|[A-Z]{1,2}', other[len(base):]):
                report[base].append(other)
    return {k: sorted(v) for k, v in sorted(report.items())}


def analyze_chno(channel_map):
    analysis = {}
    by_chno = defaultdict(set)
    for key, entries in channel_map.items():
        sources = [{'playlist': e['playlist'], 'tvg_chno': e['tvg_chno']} for e in entries if e.get('tvg_chno')]
        if sources:
            analysis[key] = sources
            for s in sources:
                by_chno[s['tvg_chno']].add(key)
    conflicts = {
        c: sorted(v) for c, v in by_chno.items() if len(v) > 1
    }
    conflicts = dict(sorted(conflicts.items(), key=lambda kv: (not kv[0].isdigit(), int(kv[0]) if kv[0].isdigit() else 0, kv[0])))
    return dict(sorted(analysis.items())), conflicts


collisions = find_name_collisions(channels.keys())
chno_analysis, chno_conflicts = analyze_chno(channels)
COLLISION_FILE.write_text(json.dumps(collisions, indent=2, ensure_ascii=False), encoding='utf-8')
CHNO_ANALYSIS_FILE.write_text(json.dumps(chno_analysis, indent=2, ensure_ascii=False), encoding='utf-8')
CHNO_CONFLICTS_FILE.write_text(json.dumps(chno_conflicts, indent=2, ensure_ascii=False), encoding='utf-8')

print('canonical channels before:', len(raw_keys))
print('canonical channels after:', len(channels))
print('name collisions:', len(collisions))
print('tvg_chno conflicts:', len(chno_conflicts))
print('parser v3 completed')
