from pathlib import Path
import json
import re
from collections import defaultdict, Counter

import yaml

from keys import canonical_key, clean_display_name
from channel_identity import (
    build_alias_map, is_technical_id, pick_editorial_name, resolve_missing_names,
)

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / 'cache' / 'playlists'
OUTPUT_DIR = BASE_DIR / 'output'
OUTPUT_DIR.mkdir(exist_ok=True)
SOURCES_FILE = BASE_DIR / 'config' / 'sources.yaml'
ALIASES_FILE = BASE_DIR / 'config' / 'channel_aliases.json'

OUT_FILE = OUTPUT_DIR / 'canonical_channels.json'
OUT_STATS = OUTPUT_DIR / 'canonical_stats.json'
EPG_FILE = OUTPUT_DIR / 'epg_sources.json'
REPORT_FILE = OUTPUT_DIR / 'canonicalization_report.json'
ALIAS_MAP_FILE = OUTPUT_DIR / 'alias_map.json'
QUARANTINE_FILE = OUTPUT_DIR / 'quarantine_technical.json'
COLLISION_FILE = OUTPUT_DIR / 'canonical_collision_report.json'
CHNO_CONFLICTS_FILE = OUTPUT_DIR / 'chno_conflicts.json'
CHNO_ANALYSIS_FILE = OUTPUT_DIR / 'chno_analysis.json'


def extract_urls(text):
    if not text:
        return []
    return re.findall(r'https?://[^\s"<>]+', text, flags=re.IGNORECASE)


def extract_first_url(text):
    urls = extract_urls(text)
    return urls[0] if urls else ''


def extract_epg_urls(text, url_filter=None):
    urls = [u.rstrip(',;') for u in extract_urls(text)]
    return list(dict.fromkeys([
        u for u in urls
        if ('.xml' in u.lower() or '.gz' in u.lower()) and (not url_filter or re.search(url_filter, u))
    ]))


def extract_attr(line, name):
    m = re.search(rf'(?<![\w-]){re.escape(name)}="([^"]*)', line)
    return m.group(1).strip() if m else ''


# varianti usate dalle sorgenti per gli stessi attributi (es. "tvgid", "vg-id" e "tvg_logo" in TOPTiVU/IPTVItalia)
ATTR_ALIASES = {
    'tvg-id': ('tvg-id', 'tvgid', 'tvg_id', 'vg-id'),
    'tvg-logo': ('tvg-logo', 'tvg_logo'),
}


def extract_first_attr(line, canonical):
    for name in ATTR_ALIASES.get(canonical, (canonical,)):
        value = extract_attr(line, name)
        if value:
            return value
    return ''


def entry_key(entry):
    # un identificativo tecnico non puo' fare da chiave: si usa il nome editoriale
    tvg_id = '' if is_technical_id(entry['tvg_id']) else entry['tvg_id']
    tvg_name = '' if is_technical_id(entry['tvg_name']) else entry['tvg_name']
    return canonical_key(tvg_id, tvg_name, entry['editorial_name'])


def parse_playlist(lines, pname, include):
    entries = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith('#EXTINF'):
            continue
        if include == 'country_it' and 'tvg-country="IT"' not in line:
            continue

        url = ''
        # ignora tutte le righe metadata (#EXTVLCOPT, #KODIPROP, ecc.)
        for j in range(i + 1, min(i + 15, len(lines))):
            candidate_line = lines[j].strip()
            if not candidate_line or candidate_line.startswith('#'):
                continue
            candidate = extract_first_url(candidate_line)
            if candidate.startswith('http'):
                url = candidate
                break

        tvg_name = extract_attr(line, 'tvg-name')
        display_name = line.rsplit(',', 1)[-1].strip()
        entries.append({
            'playlist': pname,
            'display_name': display_name,
            'tvg_id': extract_first_attr(line, 'tvg-id'),
            'tvg_name': tvg_name,
            'tvg_logo': extract_first_url(extract_first_attr(line, 'tvg-logo')),
            'tvg_chno': extract_attr(line, 'tvg-chno'),
            'group_title': extract_attr(line, 'group-title'),
            'url': url,
            'editorial_name': pick_editorial_name(tvg_name, display_name),
        })
    return entries


def resolve_aliases(config_aliases, generated):
    """Combina override manuali e alias generati; le catene vengono risolte fino alla chiave finale."""
    merged = {**generated, **config_aliases}

    def final(key, seen=()):
        target = merged.get(key, key)
        if target == key or target in seen:
            return target
        return final(target, seen + (key,))

    return {k: final(k) for k in merged if final(k) != k}


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
    conflicts = {c: sorted(v) for c, v in by_chno.items() if len(v) > 1}
    conflicts = dict(sorted(conflicts.items(), key=lambda kv: (not kv[0].isdigit(), int(kv[0]) if kv[0].isdigit() else 0, kv[0])))
    return dict(sorted(analysis.items())), conflicts


def dedupe_by_url(entries):
    seen = {}
    out = []
    for e in entries:
        u = e['url']
        if u in seen:
            kept = seen[u]
            for field in ('tvg_logo', 'tvg_chno', 'group_title', 'tvg_id'):
                if not kept.get(field) and e.get(field):
                    kept[field] = e[field]
            # logo e tvg-id di ogni sorgente restano disponibili per le rispettive priorita'
            if e.get('tvg_logo'):
                kept.setdefault('logos', {}).setdefault(e['playlist'], e['tvg_logo'])
            if e.get('tvg_id'):
                kept.setdefault('tvg_ids', {}).setdefault(e['playlist'], e['tvg_id'])
            if e.get('group_title'):
                kept.setdefault('groups', {}).setdefault(e['playlist'], e['group_title'])
            continue
        seen[u] = e
        out.append(e)
    return out


def main():
    sources = yaml.safe_load(SOURCES_FILE.read_text(encoding='utf-8'))['sources']
    config_aliases = json.loads(ALIASES_FILE.read_text(encoding='utf-8')) if ALIASES_FILE.exists() else {}

    all_entries = []
    stats = {}
    epg_sources = {}
    epg_order = []

    for src in sources:
        pname = src['name']
        playlist = CACHE_DIR / f'{pname}.m3u'
        if not playlist.exists():
            print(f'[WARN] playlist mancante: {pname}')
            continue
        lines = playlist.read_text(encoding='utf-8', errors='ignore').splitlines()

        if src.get('epg_from_header') and lines:
            urls = extract_epg_urls('\n'.join(lines[:10]), src.get('epg_url_filter'))
            if urls:
                epg_sources[pname] = urls
                epg_order.append((src.get('epg_priority', 99), pname))

        entries = [e for e in parse_playlist(lines, pname, src.get('include', 'all')) if e['url']]
        all_entries.extend(entries)
        stats[pname] = len(entries)

    resolve_missing_names(all_entries)

    quarantine = [
        {k: e[k] for k in ('playlist', 'display_name', 'tvg_id', 'tvg_name', 'url')}
        for e in all_entries if not e['editorial_name']
    ]
    all_entries = [e for e in all_entries if e['editorial_name']]

    channels = defaultdict(list)
    raw_keys = set()
    for e in all_entries:
        key = entry_key(e)
        if not key:
            continue
        raw_keys.add(key)
        channels[key].append(e)

    generated = build_alias_map(channels)
    alias_map = resolve_aliases(config_aliases, generated)
    merged = defaultdict(list)
    for key, entries in channels.items():
        merged[alias_map.get(key, key)].extend(entries)
    channels = {k: dedupe_by_url(v) for k, v in sorted(merged.items())}

    # i riferimenti XMLTV seguono la priorita' delle sorgenti (epg_priority)
    all_epg = list(dict.fromkeys(u for _, name in sorted(epg_order) for u in epg_sources[name]))
    preferred = all_epg[0] if all_epg else ''
    epg_sources['_all'] = all_epg
    epg_sources['_preferred'] = preferred

    def write(path, obj):
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')

    write(OUT_FILE, channels)
    write(OUT_STATS, {'channels': len(channels), 'playlist_stats': stats})
    write(EPG_FILE, epg_sources)
    write(REPORT_FILE, dict(sorted(alias_map.items())))
    write(ALIAS_MAP_FILE, dict(sorted(generated.items())))
    write(QUARANTINE_FILE, quarantine)

    collisions = find_name_collisions(channels.keys())
    chno_analysis, chno_conflicts = analyze_chno(channels)
    write(COLLISION_FILE, collisions)
    write(CHNO_ANALYSIS_FILE, chno_analysis)
    write(CHNO_CONFLICTS_FILE, chno_conflicts)

    print('canonical channels before:', len(raw_keys))
    print('canonical channels after:', len(channels))
    print('entries without editorial name (excluded):', len(quarantine))
    print('name collisions:', len(collisions))
    print('parser v4 completed')


if __name__ == '__main__':
    main()
