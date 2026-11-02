from pathlib import Path
import json

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

CHANNELS_FILE = OUTPUT_DIR / "canonical_channels.json"
PROBE_FILE = OUTPUT_DIR / "probe_results.json"
LCN_FILE = BASE_DIR / "config" / "lcn_nazionale.json"
LCN_REPORT_FILE = OUTPUT_DIR / "lcn_assignment_report.json"

SELECTED_FILE = OUTPUT_DIR / "selected_channels.json"
PLAYLIST_FILE = OUTPUT_DIR / "final_playlist.m3u"
PLAYLIST_FULL_FILE = OUTPUT_DIR / "final_playlist_full.m3u"

LOGO_PRIORITY = ["IPTVItalia", "FreeTV", "OpenIPTVItaly", "TOPTiVU", "DavideFulgione"]

with open(LCN_FILE, 'r', encoding='utf-8') as f:
    lcn_table = json.load(f)

with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
    channels = json.load(f)

with open(PROBE_FILE, 'r', encoding='utf-8') as f:
    probes = json.load(f)

ALIASES_FILE = BASE_DIR / "config" / "channel_aliases.json"
aliases = json.loads(ALIASES_FILE.read_text(encoding='utf-8')) if ALIASES_FILE.exists() else {}


def apply_aliases(channel_map, probe_map):
    # merges verified alias channels into their target before LCN matching
    merged_channels = {}
    merged_probes = {}
    applied = {}
    for name in channel_map:
        target = aliases.get(name, name)
        if target != name:
            applied.setdefault(target, []).append(name)
        merged_channels.setdefault(target, []).extend(channel_map[name])
        merged_probes.setdefault(target, []).extend(probe_map.get(name, []))
    for target in applied:
        seen = set()
        unique = []
        for x in merged_probes[target]:
            key = (x.get('url'), x.get('playlist'))
            if key not in seen:
                seen.add(key)
                unique.append(x)
        merged_probes[target] = sorted(unique, key=lambda x: x.get('score', 0), reverse=True)
    return merged_channels, merged_probes, applied


channels, probes, alias_applied = apply_aliases(channels, probes)

selected = {}

for channel, entries in channels.items():
    logo = ''
    # playlist tvg_chno values are ignored: the national LCN table is the only numbering source
    chno = lcn_table.get(channel, '')

    for src in LOGO_PRIORITY:
        hit = next((e for e in entries if e.get('playlist') == src and e.get('tvg_logo')), None)
        if hit:
            logo = hit.get('tvg_logo', '')
            break

    ranked = probes.get(channel, [])

    best = ranked[0] if len(ranked) > 0 else None
    backup1 = ranked[1] if len(ranked) > 1 else None
    backup2 = ranked[2] if len(ranked) > 2 else None
    fallback = [x for x in ranked if x.get('score', 0) == 0]

    selected[channel] = {
        'channel': channel,
        'tvg_logo': logo,
        'tvg_chno': chno,
        'best': best,
        'backup1': backup1,
        'backup2': backup2,
        'fallback': fallback,
    }

with_chno = []
without_chno = []

for item in selected.values():
    if item.get('tvg_chno') != '':
        with_chno.append(item)
    else:
        without_chno.append(item)

with_chno.sort(key=lambda x: (x['tvg_chno'], x['channel']))
without_chno.sort(key=lambda x: x['channel'])
ordered = with_chno + without_chno

m3u = ['#EXTM3U']
m3u_full = ['#EXTM3U']

for item in ordered:
    best = item.get('best')
    if best and best.get('url'):
        m3u.append(
            f'#EXTINF:-1 tvg-chno="{item.get("tvg_chno","")}" tvg-logo="{item.get("tvg_logo","")}",{item["channel"]}'
        )
        m3u.append(best['url'])

    sources = [item.get('best'), item.get('backup1'), item.get('backup2')]
    sources.extend(item.get('fallback', []))

    for src in [s for s in sources if s and s.get('url')]:
        m3u_full.append(
            f'#EXTINF:-1 tvg-chno="{item.get("tvg_chno","")}" tvg-logo="{item.get("tvg_logo","")}",{item["channel"]} [{src.get("playlist","")}]'
        )
        m3u_full.append(src['url'])

SELECTED_FILE.write_text(json.dumps(selected, indent=2, ensure_ascii=False), encoding='utf-8')
PLAYLIST_FILE.write_text('\n'.join(m3u), encoding='utf-8')
PLAYLIST_FULL_FILE.write_text('\n'.join(m3u_full), encoding='utf-8')
LCN_REPORT_FILE.write_text(json.dumps({
    'assigned_from_lcn_table': len(with_chno),
    'without_lcn': len(without_chno),
    'aliases_applied': alias_applied,
}, indent=2, ensure_ascii=False), encoding='utf-8')

print('assigned from LCN table:', len(with_chno))
print('without LCN:', len(without_chno))
print('selector completed')
