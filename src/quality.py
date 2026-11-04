"""Quality Selection: misura il contenuto video reale (frame duplicati, content fps) e riordina i candidati.

Il prober verifica solo che lo stream si apra; qui si guarda cosa contiene davvero. Uno stream a
25 fps nominali con il 70% di frame duplicati supera ogni controllo di bitrate/timestamp ma e' scattoso.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import math
import os
import re
import subprocess
import time

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
CACHE_FILE = OUTPUT_DIR / "quality_cache.json"

FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
DURATION = int(os.environ.get("QUALITY_SECONDS", "20"))
WORKERS = int(os.environ.get("QUALITY_WORKERS", "4"))
BUDGET_S = int(os.environ.get("QUALITY_BUDGET_S", "5400"))  # solo per i canali senza LCN
NON_LCN_TOP_N = int(os.environ.get("QUALITY_NON_LCN_TOP_N", "3"))
CACHE_TTL_S = int(os.environ.get("QUALITY_CACHE_TTL_H", "12")) * 3600
CACHE_PRUNE_S = 7 * 24 * 3600

DUP_YDIF = 0.05         # YDIF sotto soglia = frame identico al precedente
SKIP_FRAMES = 5         # i primi frame dopo l'apertura non sono affidabili
MIN_FRAMES = 100        # sotto questo numero il test non e' valutabile
DEGRADED_DUP, DEGRADED_FPS = 0.15, 20.0
BAD_DUP, BAD_FPS = 0.35, 15.0
STATIC_DUP = 0.95       # quasi tutto identico: slate/scena statica, non e' un difetto dello stream
PROMOTE_GEOBLOCKED = os.environ.get("QUALITY_PROMOTE_GEOBLOCKED", "1") != "0"
ORDER = {"good": 0, "unknown": 1, "degraded": 2, "bad": 3}

_YDIF = re.compile(r"lavfi\.signalstats\.YDIF=([-+0-9.eE]+|nan|inf)")
_PTS = re.compile(r"pts_time:([-+0-9.eE]+|nan|inf)")
_VF = "scale='min(1280,iw)':-2,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=-"


def target_url(stream):
    """URL realmente riproducibile: per i manifest recuperati (mediapolis/la7) e' final_url."""
    if str(stream.get("probe_source", "")).startswith(("mediapolis:", "la7:")):
        return stream.get("final_url") or stream["url"]
    return stream["url"]


def classify(dup_ratio, content_fps, frames):
    if frames < MIN_FRAMES or dup_ratio >= STATIC_DUP:
        return "unknown"
    if dup_ratio >= BAD_DUP and content_fps < BAD_FPS:
        return "bad"
    if dup_ratio >= DEGRADED_DUP and content_fps < DEGRADED_FPS:
        return "degraded"
    return "good"


def score(dup_ratio, content_fps):
    """100 = nessun duplicato; altrimenti proporzionale al content fps (25 fps -> 100, 5 fps -> 0)."""
    if dup_ratio < DEGRADED_DUP:
        return 100
    return max(0, min(100, round(100 * (content_fps - 5) / 20)))


def _run(url):
    cmd = [FFMPEG, "-nostdin", "-v", "error", "-i", url, "-t", str(DURATION), "-an",
           "-vf", _VF, "-f", "null", "-"]
    base = {"quality_checked": True, "quality_measured_at": int(time.time())}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=DURATION * 4 + 40)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {**base, "quality_status": "unknown", "quality_score": None,
                "frames_sampled": 0, "quality_error": type(exc).__name__}
    errors = [l for l in proc.stderr.splitlines() if l.strip()]
    ydif, pts = [], []
    for line in proc.stdout.splitlines():
        m = _YDIF.search(line)
        if m:
            ydif.append(float(m.group(1)))
            continue
        m = _PTS.search(line)
        if m:
            pts.append(float(m.group(1)))
    ydif, pts = ydif[SKIP_FRAMES:], pts[SKIP_FRAMES:]
    ydif = [v for v in ydif if not math.isnan(v)]
    if not ydif:
        return {**base, "quality_status": "unknown", "quality_score": None, "frames_sampled": 0,
                "quality_error": (errors[-1][:160] if errors else "no_frames")}
    dups = [v < DUP_YDIF for v in ydif]
    longest = run = 0
    for d in dups:
        run = run + 1 if d else 0
        longest = max(longest, run)
    dup_ratio = sum(dups) / len(dups)
    span = pts[-1] - pts[0] if len(pts) > 1 and pts[-1] > pts[0] else 0
    nominal_fps = (len(pts) - 1) / span if span else 25.0
    content_fps = nominal_fps * (1 - dup_ratio)
    ordered = sorted(ydif)
    return {**base,
            "frames_sampled": len(ydif),
            "nominal_fps": round(nominal_fps, 1),
            "dup_ratio": round(dup_ratio, 3),
            "content_fps": round(content_fps, 1),
            "ydif_median": round(ordered[len(ordered) // 2], 3),
            "longest_dup_run": longest,
            "decode_errors": len(errors),
            "quality_score": score(dup_ratio, content_fps),
            "quality_status": classify(dup_ratio, content_fps, len(ydif)),
            "quality_error": None}


def measure(url):
    """Una misura; se lo stream e' sospetto (o non ha dato frame) ne esegue una seconda e tiene la migliore."""
    first = _run(url)
    first["quality_attempts"] = 1
    suspicious = first.get("frames_sampled", 0) == 0 or first.get("dup_ratio", 0) >= DEGRADED_DUP
    if not suspicious:
        return first
    second = _run(url)
    second["quality_attempts"] = 2
    best = max((first, second), key=lambda r: (r.get("frames_sampled", 0) > 0, -r.get("dup_ratio", 1)))
    best["quality_attempts"] = 2
    return best


def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check_urls(lcn_urls, other_urls):
    """Misura gli URL non in cache; i canali LCN non hanno limite di tempo, gli altri rispettano BUDGET_S."""
    cache, now = load_cache(), time.time()
    cache = {u: r for u, r in cache.items() if now - r.get("quality_measured_at", 0) < CACHE_PRUNE_S}

    def stale(u):
        return now - cache.get(u, {}).get("quality_measured_at", 0) >= CACHE_TTL_S

    lcn_todo = [u for u in lcn_urls if stale(u)]
    other_todo = [u for u in other_urls if stale(u) and u not in set(lcn_todo)]
    deadline = now + BUDGET_S

    def run(job):
        url, hard = job
        if not hard and time.time() >= deadline:
            return url, None
        return url, measure(url)

    jobs = [(u, True) for u in lcn_todo] + [(u, False) for u in other_todo]
    skipped = 0
    if jobs:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for url, res in pool.map(run, jobs):
                if res is None:
                    skipped += 1
                else:
                    cache[url] = res
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache, {"measured_now": len(jobs) - skipped, "skipped_budget": skipped}


def reorder(streams):
    """Gli stream 'ok' vanno per classe di qualita' (good > unknown > degraded > bad), poi nell'ordine di ranking
    originale; tra i soli 'bad' vince il quality_score piu' alto. I non verificati restano sempre in coda."""
    ok = [s for s in streams if s.get("probe_status") == "ok"]
    rest = [s for s in streams if s.get("probe_status") != "ok"]

    def key(s):
        status = s.get("quality_status") or "unknown"
        return (ORDER.get(status, 1), -(s.get("quality_score") or 0) if status == "bad" else 0)

    return sorted(ok, key=key) + rest


def _brief(s):
    return {k: s.get(k) for k in ("url", "playlist", "probe_source", "quality_status", "quality_score",
                                  "content_fps", "dup_ratio", "width", "height")}


def apply_quality(items, set_streams, is_official=None):
    """Misura i candidati e riordina gli stream di ogni item. Ritorna il report (quality_report.json)."""
    started = time.time()
    lcn_urls, other_urls = {}, {}
    for item in items:
        ok = [s for s in item["streams"] if s.get("probe_status") == "ok"]
        if item.get("lcn") is not None:
            lcn_urls.update({target_url(s): True for s in ok})
        else:
            other_urls.update({target_url(s): True for s in ok[:NON_LCN_TOP_N]})
    cache, run_info = check_urls(list(lcn_urls), list(other_urls))

    promotions, all_bad, degraded_primary, unmeasured_primary = [], [], [], 0
    counts = {"good": 0, "degraded": 0, "bad": 0, "unknown": 0}
    for item in items:
        for s in item["streams"]:
            res = cache.get(target_url(s))
            if res and s.get("probe_status") == "ok":
                s.update(res)
        measured = [s for s in item["streams"] if s.get("quality_status")]
        for s in measured:
            counts[s["quality_status"]] += 1
        old = item["streams"]
        new = reorder(old)
        label = {"channel": item["channel"], "lcn": item.get("lcn")}
        ok_streams = [s for s in new if s.get("probe_status") == "ok"]
        everything_bad = bool(ok_streams) and all(s.get("quality_status") == "bad" for s in ok_streams)
        if everything_bad:
            all_bad.append({**label, "kept": _brief(new[0]), "ok_candidates": len(ok_streams),
                            "unverified_candidates": len(new) - len(ok_streams),
                            "candidates": [_brief(s) for s in ok_streams]})
        elif ok_streams and ok_streams[0].get("quality_status") == "degraded":
            degraded_primary.append({**label, "primary": _brief(ok_streams[0])})
        elif ok_streams and ok_streams[0].get("quality_status") in (None, "unknown"):
            unmeasured_primary += 1
        unverified = None
        if everything_bad and PROMOTE_GEOBLOCKED and is_official:
            # 403 dal runner USA = geoblocco: lo stream ufficiale non e' verificabile qui ma e' quasi sempre
            # riproducibile in Italia; in questo solo caso (tutti i verificati sono bad) lo si preferisce.
            geo = [s for s in new if s.get("probe_status") == "forbidden" and is_official(s["url"])]
            if geo:
                unverified = geo[0]
                unverified["quality_note"] = "promoted_unverified_geoblocked"
                new = [unverified] + [s for s in new if s is not unverified]
        if new[0] is not old[0]:
            promotions.append({**label, "from": _brief(old[0]), "to": _brief(new[0]),
                               "verified": unverified is None})
        if any(a is not b for a, b in zip(new, old)):
            set_streams(item, new)
    return {
        "generated_at": int(time.time()),
        "config": {"seconds": DURATION, "workers": WORKERS, "non_lcn_top_n": NON_LCN_TOP_N,
                   "cache_ttl_h": CACHE_TTL_S // 3600,
                   "thresholds": {"bad": {"dup_ratio": BAD_DUP, "content_fps": BAD_FPS},
                                  "degraded": {"dup_ratio": DEGRADED_DUP, "content_fps": DEGRADED_FPS}}},
        "summary": {"channels": len(items),
                    "lcn_channels": sum(1 for i in items if i.get("lcn") is not None),
                    "lcn_urls": len(lcn_urls), "other_urls": len(other_urls), **run_info,
                    "status_counts": counts, "promotions": len(promotions),
                    "all_bad": len(all_bad), "degraded_primary": len(degraded_primary),
                    "primary_unmeasured": unmeasured_primary,
                    "duration_s": round(time.time() - started)},
        "promotions": promotions,
        "all_bad": all_bad,
        "degraded_primary": degraded_primary,
    }
