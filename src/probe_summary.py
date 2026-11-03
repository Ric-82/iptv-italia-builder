"""Riepilogo diagnostico del probing (sola lettura: non altera prober ne' selezione)."""
import json
import re
import sys
from collections import Counter
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / 'output'


def summarize(results, stats, published_urls):
    streams = [s for entries in results.values() for s in entries]
    status = Counter(s.get('probe_status') or 'n/d' for s in streams)
    valid = status.get('ok', 0)
    urls = {s.get('url') for s in streams}
    return {
        'analizzati': len(streams),
        'validi': valid,
        'falliti': len(streams) - valid,
        'falliti_per_stato': {k: v for k, v in sorted(status.items()) if k != 'ok'},
        # analizzati ma non presenti in final_playlist_full.m3u (deduplicati o assorbiti da altri canali)
        'scartati': len(urls - published_urls),
        'cache_hit': stats.get('cache_hits', 0),
        'recuperati_da_manifest': {k: stats.get(k, 0) for k in
                                   ('hls_recovered', 'dash_recovered', 'mediapolis_recovered', 'la7_recovered')},
    }


def main():
    results = json.loads((OUTPUT_DIR / 'probe_results.json').read_text(encoding='utf-8'))
    stats_file = OUTPUT_DIR / 'probe_stats.json'
    stats = json.loads(stats_file.read_text(encoding='utf-8')) if stats_file.exists() else {}
    full = OUTPUT_DIR / 'final_playlist_full.m3u'
    published = set(re.findall(r'^https?://\S+$', full.read_text(encoding='utf-8'), re.M)) if full.exists() else set()
    summary = summarize(results, stats, published)
    print('== Riepilogo probing')
    for key in ('analizzati', 'validi', 'falliti', 'scartati', 'cache_hit'):
        print(f'  stream {key}: {summary[key]}' if key != 'cache_hit' else f'  cache hit: {summary[key]}')
    print(f'  falliti per stato: {summary["falliti_per_stato"]}')
    print(f'  recuperati da manifest: {summary["recuperati_da_manifest"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
