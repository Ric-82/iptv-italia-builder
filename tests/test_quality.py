import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import epg_check
import quality


def stream(url, status="ok", q=None, score=None):
    s = {"url": url, "probe_status": status}
    if q:
        s.update({"quality_status": q, "quality_score": score})
    return s


def run(streams, lcn=1, promote_geo=False, official=None):
    item = {"channel": "X", "lcn": lcn, "streams": streams}
    cache = {s["url"]: {k: s[k] for k in ("quality_status", "quality_score") if k in s} for s in streams}
    quality.check_urls = lambda lcn_urls, other_urls: (cache, {"measured_now": 0, "skipped_budget": 0})
    quality.PROMOTE_GEOBLOCKED = promote_geo

    def set_streams(it, new):
        it["streams"] = new

    report = quality.apply_quality([item], set_streams, official)
    return [s["url"] for s in item["streams"]], report


def test_case_a_bad_then_good():
    order, report = run([stream("a", q="bad", score=0), stream("b", q="good", score=100)])
    assert order == ["b", "a"]
    assert report["summary"]["promotions"] == 1 and report["summary"]["all_bad"] == 0


def test_case_b_bad_degraded_good():
    order, _ = run([stream("a", q="bad", score=0), stream("b", q="degraded", score=60),
                    stream("c", q="good", score=100)])
    assert order == ["c", "b", "a"]


def test_case_c_all_bad_keeps_best():
    order, report = run([stream("a", q="bad", score=10), stream("b", q="bad", score=40)])
    assert order == ["b", "a"]
    assert report["summary"]["all_bad"] == 1 and report["all_bad"][0]["kept"]["url"] == "b"


def test_unknown_beats_degraded_but_not_good():
    order, _ = run([stream("a", q="degraded", score=60), stream("b", q="unknown"), stream("c", q="good", score=100)])
    assert order == ["c", "b", "a"]


def test_failed_streams_never_promoted_without_geoblock_rule():
    order, report = run([stream("a", q="bad", score=0), stream("f", status="forbidden"),
                         stream("u", status="unreachable")])
    assert order == ["a", "f", "u"]
    assert report["summary"]["promotions"] == 0 and report["summary"]["all_bad"] == 1


def test_geoblocked_official_promoted_only_when_all_verified_bad():
    official = lambda url: url.startswith("rai")
    order, report = run([stream("a", q="bad", score=0), stream("rai-geo", status="forbidden"),
                         stream("other", status="forbidden")], promote_geo=True, official=official)
    assert order == ["rai-geo", "a", "other"]
    assert report["promotions"][0]["verified"] is False
    order, _ = run([stream("a", q="bad", score=0), stream("b", q="good", score=100),
                    stream("rai-geo", status="forbidden")], promote_geo=True, official=official)
    assert order == ["b", "a", "rai-geo"]


def test_classify_and_score():
    assert quality.classify(0.0, 25.0, 300) == "good"
    assert quality.classify(0.74, 6.5, 300) == "bad"
    assert quality.classify(0.2, 20 * 0.8, 300) == "degraded"
    assert quality.classify(0.5, 25.0, 300) == "good"       # 50 fps con frame raddoppiati: contenuto regolare
    assert quality.classify(0.99, 0.2, 300) == "unknown"     # slate statico
    assert quality.classify(0.7, 7.0, 50) == "unknown"       # pochi frame
    assert quality.score(0.74, 6.5) < 10 and quality.score(0.0, 25.0) == 100


def test_ydif_regex_handles_scientific_notation():
    assert float(quality._YDIF.search("lavfi.signalstats.YDIF=4.16667e-05").group(1)) < quality.DUP_YDIF


def test_epg_filter(monkeypatch):
    def fake(url):
        return {"url": url, "alive": "good" in url, "reason": "ok" if "good" in url else "http_404", "attempts": 1}
    monkeypatch.setattr(epg_check, "check_url", fake)
    alive, report = epg_check.filter_reachable(["http://good/1", "http://dead/2", "http://good/3"])
    assert alive == ["http://good/1", "http://good/3"]
    assert report["excluded"] == [{"url": "http://dead/2", "reason": "http_404"}]
    alive, report = epg_check.filter_reachable(["http://dead/1"])
    assert alive == ["http://dead/1"] and report["all_unreachable_kept_original"]


def test_epg_content_validation():
    import gzip
    assert epg_check._valid_xmltv(gzip.compress(b'<?xml version="1.0"?><tv></tv>'), "x.xml.gz")
    assert not epg_check._valid_xmltv(b"<html><body>Not Found</body></html>", "x.xml")
