from pathlib import Path
import json
import re
import time
from datetime import datetime, timezone
import requests

USER_AGENT = "Mozilla/5.0 (m3u-ip.tv 3.1.0) Browser"


def raw_url(url):
    # i link "blob" di GitHub restituiscono HTML: si scarica il file raw
    m = re.match(r'https://github\.com/([^/]+)/([^/]+)/blob/(.+)$', url)
    return f'https://raw.githubusercontent.com/{m[1]}/{m[2]}/{m[3]}' if m else url


def download_playlists(sources, cache_dir, retries=3):
    cache_dir.mkdir(parents=True, exist_ok=True)
    report = {}

    headers = {"User-Agent": USER_AGENT}

    for source in sources:
        name = source["name"]
        url = raw_url(source["url"])
        target = cache_dir / f"{name}.m3u"
        now = datetime.now(timezone.utc).isoformat()

        error = None
        for attempt in range(retries):
            try:
                r = requests.get(url, headers=headers, timeout=30)
                r.raise_for_status()
                # una pagina di errore non deve sovrascrivere l'ultima playlist valida in cache
                if '#EXTM3U' not in r.text[:200] and '#EXTINF' not in r.text[:2000]:
                    raise ValueError('contenuto non M3U')
                target.write_text(r.text, encoding="utf-8", errors="ignore")
                report[name] = {"status": "ok", "size": len(r.text), "downloaded_at": now}
                print(f"[OK] salvato {target.name}")
                error = None
                break
            except Exception as exc:
                error = exc
                time.sleep(2 * (attempt + 1))

        if error is not None:
            report[name] = {"status": "error", "error": str(error), "downloaded_at": now}
            print(f"[ERRORE] {name}: {error}")

    (cache_dir / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
