# prober v7.3
# Mediapolis HLS/DASH (HbbTV) detection, La7 redirect recovery, cache and incremental saving.

from pathlib import Path
import os
import json
import subprocess
import time
import argparse
import re
import requests
import xml.etree.ElementTree as ET

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT = BASE_DIR / 'output'
CHANNELS_FILE = OUTPUT / 'canonical_channels.json'
RESULTS_FILE = OUTPUT / 'probe_results.json'
CACHE_FILE = OUTPUT / 'probe_cache.json'
STATS_FILE = OUTPUT / 'probe_stats.json'

FFPROBE = os.environ.get('FFPROBE_PATH', 'ffprobe')
CACHE_MAX_AGE = 24 * 3600
TIMEOUT = 8

MEDIAPOLIS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0',
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'it-IT,it;q=0.9'
}


def compute_score(width, height, bitrate):
    score = (width or 0) * (height or 0)
    try:
        score += int(bitrate)
    except:
        pass
    return score


def parse_hls_text(text):
    variants = []
    for line in text.splitlines():
        if not line.startswith('#EXT-X-STREAM-INF'):
            continue
        bw = re.search(r'BANDWIDTH=(\d+)', line)
        res = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
        fps = re.search(r'FRAME-RATE=([0-9.]+)', line)
        codecs = re.search(r'CODECS="([^"]+)"', line)
        bitrate = int(bw.group(1)) if bw else 0
        width = int(res.group(1)) if res else 0
        height = int(res.group(2)) if res else 0
        variants.append({
            'width': width,
            'height': height,
            'fps': fps.group(1) if fps else None,
            'codec': codecs.group(1) if codecs else 'manifest-hls',
            'bitrate': bitrate,
            'score': compute_score(width, height, bitrate)
        })
    return max(variants, key=lambda x: x['score']) if variants else None


last_http_status = None


def http_get(url, headers=None, timeout=10):
    # records the outcome of the last HTTP request in last_http_status
    global last_http_status
    last_http_status = None
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.exceptions.Timeout:
        last_http_status = 'timeout'
        return None
    except requests.exceptions.RequestException:
        last_http_status = 'unreachable'
        return None
    if r.status_code == 200:
        last_http_status = 'ok'
        return r
    if r.status_code == 403:
        last_http_status = 'forbidden'
    elif r.status_code >= 400:
        last_http_status = 'unreachable'
    else:
        last_http_status = 'unclassified'
    return None


def probe_hls_manifest(url):
    r = http_get(url, timeout=10)
    return parse_hls_text(r.text) if r is not None else None


def parse_dash_text(text):
    try:
        root = ET.fromstring(text.strip())
    except:
        return None
    reps = []
    for elem in root.iter():
        if not elem.tag.endswith('AdaptationSet'):
            continue
        ctype = elem.attrib.get('contentType') or elem.attrib.get('mimeType', '')
        if ctype and not ctype.startswith('video'):
            continue
        for child in elem:
            if not child.tag.endswith('Representation'):
                continue
            rmime = child.attrib.get('mimeType', '')
            if rmime and not rmime.startswith('video'):
                continue
            try:
                w = int(elem.attrib.get('width') or child.attrib.get('width') or 0)
                h = int(elem.attrib.get('height') or child.attrib.get('height') or 0)
                bw = int(child.attrib.get('bandwidth') or 0)
            except ValueError:
                continue
            fps = elem.attrib.get('frameRate') or child.attrib.get('frameRate')
            codec = elem.attrib.get('codecs') or child.attrib.get('codecs') or 'manifest-dash'
            reps.append({'width': w, 'height': h, 'fps': fps, 'codec': codec, 'bitrate': bw, 'score': compute_score(w, h, bw)})
    return max(reps, key=lambda x: x['score']) if reps else None


def parse_manifest_text(text):
    if '#EXTM3U' in text:
        parsed = parse_hls_text(text)
        return {**parsed, 'manifest_type': 'hls'} if parsed else None
    if '<MPD' in text:
        parsed = parse_dash_text(text)
        return {**parsed, 'manifest_type': 'dash'} if parsed else None
    return None


def fetch_manifest(url, headers=None):
    # follows redirects and detects the manifest type from the content, not from the URL
    r = http_get(url, headers=headers, timeout=15)
    return parse_manifest_text(r.text) if r is not None else None


def resolve_mediapolis_manifest(url):
    return fetch_manifest(url, MEDIAPOLIS_HEADERS)


def resolve_la7_manifest(url):
    return fetch_manifest(url)


def probe_dash_manifest(url):
    r = http_get(url, timeout=10)
    return parse_dash_text(r.text) if r is not None else None


parser = argparse.ArgumentParser()
parser.add_argument('--limit', type=int, default=0)
parser.add_argument('--force', action='store_true')
args = parser.parse_args()

channels = json.loads(CHANNELS_FILE.read_text(encoding='utf-8'))
cache = json.loads(CACHE_FILE.read_text(encoding='utf-8')) if CACHE_FILE.exists() else {}

results = {}
stats = {'hls_recovered':0,'dash_recovered':0,'mediapolis_recovered':0,'la7_recovered':0,'forbidden':0,'timeout':0,'unreachable':0,'unclassified':0,'cache_hits':0}
processed = 0
start=time.time()


def save_all():
    stats['duration_seconds'] = round(time.time()-start, 2)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding='utf-8')
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    STATS_FILE.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding='utf-8')


def get_cached(url):
    if args.force:
        return None
    c = cache.get(url)
    if isinstance(c, dict) and isinstance(c.get('item'), dict) and 'probe_status' in c['item'] and time.time() - c.get('time', 0) < CACHE_MAX_AGE:
        return c['item']
    return None


try:
    for channel, entries in channels.items():
        results[channel] = []
        for entry in entries:
            processed += 1
            if args.limit and processed > args.limit:
                break

            url = entry.get('url', '')
            cached = get_cached(url)
            if cached is not None:
                item = dict(entry)
                item.update(cached)
                results[channel].append(item)
                stats['cache_hits'] += 1
                continue

            item = dict(entry)
            width = height = 0
            bitrate = None
            codec = None
            ffprobe_status = None
            ffprobe_result = 'no_video'
            try:
                proc = subprocess.run([FFPROBE,'-v','quiet','-show_streams','-print_format','json',url],capture_output=True,text=True,timeout=TIMEOUT)
                data = json.loads(proc.stdout) if proc.stdout else {}
                video = next((s for s in data.get('streams',[]) if s.get('codec_type')=='video'), {})
                width = video.get('width') or 0
                height = video.get('height') or 0
                bitrate = video.get('bit_rate')
                codec = video.get('codec_name')
                ffprobe_result = 'ok' if width * height > 0 else ('no_output' if not proc.stdout else 'no_video')
            except subprocess.TimeoutExpired as ex:
                item['error'] = str(ex)
                ffprobe_status = 'timeout'
                ffprobe_result = 'timeout'
            except Exception as ex:
                item['error'] = str(ex)
                ffprobe_status = 'unreachable'
                ffprobe_result = 'error'

            score = compute_score(width, height, bitrate)
            manifest = None
            attempted = False
            manifest_kind = None
            http_status = None
            if width * height == 0:
                low = url.lower()
                attempted = any(k in low for k in ('mediapolis.rai.it', 'jmp2.uk', 'la7', '.m3u8', '.mpd'))
                if 'mediapolis.rai.it' in low:
                    manifest = resolve_mediapolis_manifest(url)
                    manifest_kind = 'mediapolis'
                    if manifest: stats['mediapolis_recovered'] += 1
                elif 'jmp2.uk' in low or 'la7' in low:
                    manifest = resolve_la7_manifest(url)
                    manifest_kind = 'la7'
                    if manifest: stats['la7_recovered'] += 1
                elif '.m3u8' in low:
                    manifest = probe_hls_manifest(url)
                    manifest_kind = 'manifest_hls'
                    if manifest: stats['hls_recovered'] += 1
                elif '.mpd' in low:
                    manifest = probe_dash_manifest(url)
                    manifest_kind = 'manifest_dash'
                    if manifest: stats['dash_recovered'] += 1
                if attempted:
                    http_status = last_http_status

            # telemetria: origine dei dati di qualita' (non influisce sul ranking)
            item['ffprobe_result'] = ffprobe_result
            if http_status is not None:
                item['http_status'] = http_status
            if manifest:
                item.pop('error', None)
                item.update(manifest)
                # per i manifest recuperati da mediapolis/la7 il tipo effettivo e' quello rilevato dal contenuto
                item['probe_source'] = manifest_kind if manifest_kind in ('manifest_hls', 'manifest_dash') \
                    else f'{manifest_kind}:{manifest.get("manifest_type", "?")}'
                status = 'ok'
            else:
                item['score'] = score
                if width * height > 0:
                    item.update({'width': width, 'height': height, 'bitrate': bitrate, 'codec': codec})
                    item['probe_source'] = 'ffprobe'
                    status = 'ok'
                else:
                    item['probe_source'] = 'fallback'
                    if attempted and last_http_status in ('forbidden', 'timeout', 'unreachable'):
                        status = last_http_status
                    elif attempted:
                        status = 'unclassified'
                    else:
                        status = ffprobe_status or 'unclassified'
                if status != 'ok':
                    stats[status] += 1
            item['probe_status'] = status

            results[channel].append(item)
            cache[url] = {'time': time.time(), 'item': {k: v for k, v in item.items() if k not in entry or k in ('score',)}}
            save_all()

        if args.limit and processed > args.limit:
            break
except KeyboardInterrupt:
    print('interrupted, saving partial results')

save_all()
print('prober v7.3 completed')
