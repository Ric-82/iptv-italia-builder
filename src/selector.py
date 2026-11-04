from collections import Counter
from pathlib import Path
import difflib
import json
import os
import re
import unicodedata
from urllib.parse import urlparse

import yaml

from channel_identity import is_technical_id
from keys import name_key

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

CHANNELS_FILE = OUTPUT_DIR / "canonical_channels.json"
PROBE_FILE = OUTPUT_DIR / "probe_results.json"
LCN_FILE = BASE_DIR / "state" / "lcn_official.json"
SOURCES_FILE = BASE_DIR / "config" / "sources.yaml"
NAME_ALIASES_FILE = BASE_DIR / "config" / "official_name_aliases.json"
LCN_REPORT_FILE = OUTPUT_DIR / "lcn_assignment_report.json"
UNMATCHED_FILE = OUTPUT_DIR / "lcn_unmatched_report.json"
QUARANTINE_FILE = OUTPUT_DIR / "quarantine_technical_final.json"
DISPLAY_NAMES_FILE = BASE_DIR / "config" / "display_names.json"
MERGES_FILE = BASE_DIR / "config" / "editorial_merges.json"
OFFICIAL_DOMAINS_FILE = BASE_DIR / "config" / "official_domains.yaml"
EPG_FILE = OUTPUT_DIR / "epg_sources.json"
DUPLICATE_STREAMS_FILE = OUTPUT_DIR / "lcn_duplicate_streams_removed.json"
DEDUP_REPORT_FILE = OUTPUT_DIR / "dedup_report.json"

SELECTED_FILE = OUTPUT_DIR / "selected_channels.json"
PLAYLIST_FILE = OUTPUT_DIR / "final_playlist.m3u"
PLAYLIST_FULL_FILE = OUTPUT_DIR / "final_playlist_full.m3u"

SCOPE_ORDER = {'national': 0}


def sort_text(value):
    stripped = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return stripped.casefold()


def load_official_domains(path=OFFICIAL_DOMAINS_FILE):
    if not path.exists():
        return {'margin': 0.0, 'penalty': 0.0, 'suffixes': (), 'patterns': ()}
    cfg = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    pref = cfg.get('preference', {})
    return {'margin': float(pref.get('margin', 0.0)), 'penalty': float(pref.get('penalty', 0.0)),
            'suffixes': tuple(s.lower() for s in cfg.get('suffixes', [])),
            'patterns': tuple(re.compile(p, re.I) for p in cfg.get('patterns', []))}


OFFICIAL = load_official_domains()


def is_official(url, official=None):
    official = official or OFFICIAL
    host = (urlparse(url or '').hostname or '').lower()
    return bool(host) and (any(host == s or host.endswith('.' + s) for s in official['suffixes'])
                           or any(p.search(host) for p in official['patterns']))


def rank_streams(entries, official=None):
    # flussi verificati prima, poi punteggio qualita' (compute_score del prober); l'ordine di raccolta e' lo spareggio.
    # Preferenza domini ufficiali: entro `margin` dal miglior score vince l'ufficiale; i terzi hanno `penalty` sullo score.
    official = official or OFFICIAL
    usable = [e for e in entries if e.get('url')]

    def adjusted(e):
        score = e.get('score') or 0
        return score if is_official(e['url'], official) else score * (1 - official['penalty'])

    ranked = sorted(usable, key=lambda e: (e.get('probe_status') == 'ok', adjusted(e)), reverse=True)
    verified = [e for e in ranked if e.get('probe_status') == 'ok']
    if verified and official['margin'] > 0:
        top = max(e.get('score') or 0 for e in verified)
        official_ok = [e for e in verified if is_official(e['url'], official) and (e.get('score') or 0) >= top * (1 - official['margin'])]
        if official_ok:
            first = max(official_ok, key=lambda e: e.get('score') or 0)
            ranked.remove(first)
            ranked.insert(0, first)
    return ranked


def channel_name(entries, logo_priority):
    counts = Counter(e['editorial_name'] for e in entries if e.get('editorial_name'))
    if not counts:
        return ''
    best_priority = {}
    for e in entries:
        n = e.get('editorial_name')
        if n:
            best_priority[n] = min(best_priority.get(n, 99), logo_priority.get(e.get('playlist'), 99))
    return sorted(counts, key=lambda n: (-counts[n], best_priority[n], n))[0]


def pick_logo(entries, logo_priority):
    candidates = []
    for e in entries:
        if e.get('tvg_logo'):
            candidates.append((e.get('playlist'), e['tvg_logo']))
        candidates.extend(e.get('logos', {}).items())
    candidates.sort(key=lambda c: logo_priority.get(c[0], 99))
    return candidates[0][1] if candidates else ''


GENERIC_GROUPS = {'italy', 'italia'}


def pick_epg(entries, epg_priority):
    """tvg-id e group-title dalla sorgente a priorita' EPG piu' alta; `conflict` e' True se le sorgenti divergono sul tvg-id."""
    ids, groups = [], []
    for e in entries:
        if e.get('tvg_id'):
            ids.append((e.get('playlist'), e['tvg_id']))
        ids.extend(e.get('tvg_ids', {}).items())
        if e.get('group_title'):
            groups.append((e.get('playlist'), e['group_title']))
        groups.extend(e.get('groups', {}).items())
    ids.sort(key=lambda c: epg_priority.get(c[0], 99))
    groups.sort(key=lambda c: (c[1].casefold() in GENERIC_GROUPS, epg_priority.get(c[0], 99)))
    return {'tvg_id': ids[0][1] if ids else '', 'group_title': groups[0][1] if groups else '',
            'conflict': len({v for _, v in ids}) > 1}


def set_streams(item, streams):
    item['streams'] = streams
    item['best'] = streams[0]
    item['backup1'] = streams[1] if len(streams) > 1 else None
    item['backup2'] = streams[2] if len(streams) > 2 else None
    item['fallback'] = [s for s in streams if (s.get('score') or 0) == 0]


def official_keys(entry):
    names = [entry['name']] + list(entry.get('aka', []))
    keys = []
    for n in names:
        k = name_key(n)
        if not k:
            continue
        keys.append(k)
        # i marchi Discovery sono pubblicati nelle playlist senza prefisso (es. "Discovery NOVE" -> NOVE)
        for prefix in ('WARNERBROSDISCOVERY', 'DISCOVERY'):
            if k.startswith(prefix) and len(k) - len(prefix) >= 2:
                keys.append(k[len(prefix):])
                break
        # varianti con/senza "TV" finale (es. "TELECOLOR" vs "TELECOLOR TV")
        keys.append(k + 'TV')
        if k.endswith('TV') and len(k) > 5:
            keys.append(k[:-2])
    return list(dict.fromkeys(keys))


def match_channel(entry, channel_keys, name_aliases):
    keys = official_keys(entry)
    for k in keys:
        if k in channel_keys:
            return k
    for k in keys:
        target = name_aliases.get(k)
        if target in channel_keys:
            return target
    return None


def assign_lcn(table_entries, channel_keys, name_aliases):
    """Abbina le voci ufficiali ai canali raccolti. Il nazionale ha precedenza assoluta sulla Sicilia."""
    national = [e for e in table_entries if e['scope'] == 'national']
    regional = [e for e in table_entries if e['scope'] != 'national']
    national_lcns = {e['lcn'] for e in national}

    assignments, unmatched, skipped = [], [], []
    national_channels = set()

    for e in national:
        key = match_channel(e, channel_keys, name_aliases)
        if key:
            national_channels.add(key)
            assignments.append((e, key))
        else:
            unmatched.append(e)

    for e in regional:
        key = match_channel(e, channel_keys, name_aliases)
        if not key:
            unmatched.append(e)
        elif e['lcn'] in national_lcns or key in national_channels:
            skipped.append({'entry': e, 'channel': key, 'reason': 'precedenza nazionale'})
        else:
            assignments.append((e, key))
    return assignments, unmatched, skipped


def build_playlists(channels, probes, table_entries, logo_priority, name_aliases):
    keys = set(channels)
    assignments, unmatched, skipped = assign_lcn(table_entries, keys, name_aliases)

    ranked = {k: rank_streams(probes.get(k) or channels[k]) for k in channels}
    items, official_without_stream = [], []
    numbered_channels = set()

    for e, key in assignments:
        streams = ranked[key]
        if not streams:
            official_without_stream.append({'lcn': e['lcn'], 'name': e['name'], 'channel': key})
            continue
        numbered_channels.add(key)
        items.append({'lcn': e['lcn'], 'scope': e['scope'], 'official_name': e['name'], 'channel': key})

    items.sort(key=lambda i: (i['lcn'], SCOPE_ORDER.get(i['scope'], 1), sort_text(i['channel'])))
    for key in sorted(keys - numbered_channels):
        if ranked[key]:
            items.append({'lcn': None, 'scope': None, 'official_name': None, 'channel': key})
    # i canali non numerati vanno in coda in ordine alfabetico del nome mostrato
    numbered = [i for i in items if i['lcn'] is not None]
    plain = [i for i in items if i['lcn'] is None]

    quarantined = []
    final = []
    for item in numbered + sorted(plain, key=lambda i: sort_text(channel_name(channels[i['channel']], logo_priority))):
        entries = channels[item['channel']]
        name = channel_name(entries, logo_priority)
        if not name or is_technical_id(name):
            quarantined.append({'channel': item['channel'], 'name': name})
            continue
        item['name'] = name
        item['tvg_logo'] = pick_logo(entries, logo_priority)
        set_streams(item, ranked[item['channel']])
        final.append(item)
    return final, {'unmatched': unmatched, 'skipped': skipped,
                   'official_without_stream': official_without_stream, 'quarantined': quarantined}


def esc(value):
    return str(value).replace('"', "'")


def render(final, epg_urls=()):
    header = '#EXTM3U'
    if epg_urls:
        joined = ','.join(epg_urls)
        header += f' x-tvg-url="{joined}" url-tvg="{joined}"'
    m3u = [header]
    m3u_full = [header]
    for item in final:
        chno = item['lcn'] if item['lcn'] is not None else ''
        head = (f'tvg-id="{esc(item.get("tvg_id", ""))}" tvg-chno="{chno}" tvg-name="{esc(item["name"])}" '
                f'tvg-logo="{item["tvg_logo"]}" group-title="{esc(item.get("group_title", ""))}"')
        m3u.append(f'#EXTINF:-1 {head},{item["name"]}')
        m3u.append(item['best']['url'])

        sources = [item['best'], item['backup1'], item['backup2'], *item['fallback']]
        seen = set()
        for src in sources:
            if not src or not src.get('url') or src['url'] in seen:
                continue
            seen.add(src['url'])
            m3u_full.append(f'#EXTINF:-1 {head},{item["name"]} [{src.get("playlist", "")}]')
            m3u_full.append(src['url'])
    return '\n'.join(m3u), '\n'.join(m3u_full)


def unmatched_report(unmatched, channel_keys):
    report = []
    for e in unmatched:
        key = name_key(e['name'])
        report.append({
            'lcn': e['lcn'], 'scope': e['scope'], 'name': e['name'], 'key': key,
            'closest_channels': difflib.get_close_matches(key, channel_keys, n=3, cutoff=0.8),
        })
    return report


def main():
    channels = json.loads(CHANNELS_FILE.read_text(encoding='utf-8'))
    probes = json.loads(PROBE_FILE.read_text(encoding='utf-8'))
    table = json.loads(LCN_FILE.read_text(encoding='utf-8'))
    sources = yaml.safe_load(SOURCES_FILE.read_text(encoding='utf-8'))['sources']
    logo_priority = {s['name']: s.get('logo_priority', 99) for s in sources}
    name_aliases = json.loads(NAME_ALIASES_FILE.read_text(encoding='utf-8')) if NAME_ALIASES_FILE.exists() else {}

    final, report = build_playlists(channels, probes, table['entries'], logo_priority, name_aliases)
    before = {'total': len(final), 'numbered': sum(1 for i in final if i['lcn'] is not None)}
    overrides = json.loads(DISPLAY_NAMES_FILE.read_text(encoding='utf-8')) if DISPLAY_NAMES_FILE.exists() else {}
    from cleanup import finalize
    merges = json.loads(MERGES_FILE.read_text(encoding='utf-8')) if MERGES_FILE.exists() else {}
    final, cleanup_report = finalize(final, channels, logo_priority, overrides, merges)
    quality_report = None
    if os.environ.get('QUALITY_CHECK', '1') != '0':
        import quality
        quality_report = quality.apply_quality(final, set_streams, is_official)
        (OUTPUT_DIR / 'quality_report.json').write_text(
            json.dumps(quality_report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"Quality check: {quality_report['summary']}")
    epg_priority = {s['name']: s.get('epg_priority', 99) for s in sources}
    for item in final:
        epg = pick_epg([e for key in item['members'] for e in channels[key]], epg_priority)
        item['tvg_id'], item['group_title'] = epg['tvg_id'], epg['group_title']
    epg_urls = []
    if EPG_FILE.exists():
        epg_urls = json.loads(EPG_FILE.read_text(encoding='utf-8')).get('_all', [])
    if epg_urls and os.environ.get('EPG_CHECK', '1') != '0':
        import epg_check
        epg_urls, epg_report = epg_check.filter_reachable(epg_urls)
        (OUTPUT_DIR / 'epg_report.json').write_text(
            json.dumps(epg_report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"EPG: {len(epg_urls)} raggiungibili, {len(epg_report['excluded'])} esclusi")
    m3u, m3u_full = render(final, epg_urls)

    numbered = [i for i in final if i['lcn'] is not None]
    SELECTED_FILE.write_text(json.dumps(
        {(f"{i['lcn']}:{i['channel']}" if i['lcn'] is not None else i['channel']):
         {k: v for k, v in i.items() if k != 'streams'} for i in final},
        indent=2, ensure_ascii=False), encoding='utf-8')
    DUPLICATE_STREAMS_FILE.write_text(json.dumps(
        cleanup_report['numbered_duplicates_removed'], indent=2, ensure_ascii=False), encoding='utf-8')
    DEDUP_REPORT_FILE.write_text(json.dumps({
        'before': before,
        'after': {'total': len(final), 'numbered': len(numbered)},
        'plain_removed_as_numbered': cleanup_report['plain_removed_as_numbered'],
        'plain_folded_into_numbered': cleanup_report['plain_folded_into_numbered'],
        'plain_merged': cleanup_report['plain_merged'],
        'names_normalized': cleanup_report['names_normalized'],
    }, indent=2, ensure_ascii=False), encoding='utf-8')
    PLAYLIST_FILE.write_text(m3u, encoding='utf-8')
    PLAYLIST_FULL_FILE.write_text(m3u_full, encoding='utf-8')
    QUARANTINE_FILE.write_text(json.dumps(report['quarantined'], indent=2, ensure_ascii=False), encoding='utf-8')
    UNMATCHED_FILE.write_text(json.dumps({
        'official_without_channel': unmatched_report(report['unmatched'], list(channels)),
        'official_without_stream': report['official_without_stream'],
        'skipped_regional': report['skipped'],
    }, indent=2, ensure_ascii=False), encoding='utf-8')
    LCN_REPORT_FILE.write_text(json.dumps({
        'lcn_source': 'official',
        'assigned': len(numbered),
        'national': sum(1 for i in numbered if i['scope'] == 'national'),
        'regional': sum(1 for i in numbered if i['scope'] != 'national'),
        'without_lcn': len(final) - len(numbered),
        'official_without_channel': len(report['unmatched']),
        'official_without_stream': len(report['official_without_stream']),
        'quarantined_technical': len(report['quarantined']),
    }, indent=2, ensure_ascii=False), encoding='utf-8')

    print('numbered channels:', len(numbered))
    print('without LCN:', len(final) - len(numbered))
    print('selector completed')


if __name__ == '__main__':
    main()
