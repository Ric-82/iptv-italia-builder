"""Verifica la raggiungibilita' degli URL EPG (XMLTV) prima di pubblicarli nell'intestazione #EXTM3U."""
from concurrent.futures import ThreadPoolExecutor
import time
import zlib

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; iptv-italia-builder)"}
TIMEOUT = (10, 30)  # connessione, lettura
ATTEMPTS = 3
SNIFF_BYTES = 256 * 1024


def _reason(exc):
    text = str(exc).lower()
    if isinstance(exc, (requests.Timeout,)):
        return "timeout"
    if any(k in text for k in ("name resolution", "getaddrinfo", "nodename nor servname", "name or service not known")):
        return "dns_failure"
    if isinstance(exc, requests.ConnectionError):
        return "connection_failed"
    return type(exc).__name__


def _valid_xmltv(first_bytes, url):
    """Un EPG valido e' XML (eventualmente gzip) che contiene <tv>: scarta pagine HTML servite con 200."""
    data = first_bytes
    if data[:2] == b"\x1f\x8b" or url.lower().endswith(".gz"):
        try:
            data = zlib.decompressobj(31).decompress(first_bytes)[:SNIFF_BYTES]
        except zlib.error:
            return False
    return b"<tv" in data.lstrip(b"\xef\xbb\xbf \r\n\t")[:2000].lower()


def check_url(url):
    last = "unknown"
    for attempt in range(ATTEMPTS):
        try:
            with requests.get(url, stream=True, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True) as r:
                if r.status_code != 200:
                    last = f"http_{r.status_code}"
                    if r.status_code < 500:  # 4xx e' definitivo, 5xx puo' essere transitorio
                        break
                else:
                    chunk = b""
                    for part in r.iter_content(65536):
                        chunk += part
                        if len(chunk) >= 65536:
                            break
                    if _valid_xmltv(chunk, url):
                        return {"url": url, "alive": True, "reason": "ok", "attempts": attempt + 1}
                    return {"url": url, "alive": False, "reason": "invalid_content", "attempts": attempt + 1}
        except requests.RequestException as exc:
            last = _reason(exc)
        time.sleep(2 * (attempt + 1))
    return {"url": url, "alive": False, "reason": last, "attempts": attempt + 1}


def filter_reachable(urls):
    """Ritorna (url raggiungibili nell'ordine originale, report). Se nessuno risponde (rete del runner assente)
    si conserva l'elenco originale per non svuotare l'EPG, segnalandolo nel report."""
    urls = list(dict.fromkeys(urls))
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(urls)))) as pool:
        results = list(pool.map(check_url, urls))
    alive = [r["url"] for r in results if r["alive"]]
    report = {"checked_at": int(time.time()), "results": results, "published": alive,
              "excluded": [{"url": r["url"], "reason": r["reason"]} for r in results if not r["alive"]],
              "all_unreachable_kept_original": False}
    if urls and not alive:
        report["all_unreachable_kept_original"] = True
        alive = urls
        report["published"] = alive
    return alive, report
