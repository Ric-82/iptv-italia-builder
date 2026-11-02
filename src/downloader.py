from pathlib import Path
import json
from datetime import datetime
import requests

USER_AGENT = "Mozilla/5.0 (m3u-ip.tv 3.1.0) Browser"


def download_playlists(sources, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    report = {}

    headers = {"User-Agent": USER_AGENT}

    for source in sources:
        name = source["name"]
        url = source["url"]
        target = cache_dir / f"{name}.m3u"

        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            target.write_text(r.text, encoding="utf-8", errors="ignore")

            report[name] = {
                "status": "ok",
                "size": len(r.text),
                "downloaded_at": datetime.utcnow().isoformat()
            }
            print(f"[OK] salvato {target.name}")

        except Exception as exc:
            report[name] = {
                "status": "error",
                "error": str(exc),
                "downloaded_at": datetime.utcnow().isoformat()
            }
            print(f"[ERRORE] {name}: {exc}")

    (cache_dir / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
