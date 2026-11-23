"""Client reliability: errors, retries, cache, offline mode, audit, scoring rules."""
import sys
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from fixtures import ARTICLES, MockClient  # noqa: E402
from refcheck.config import Settings, load_settings  # noqa: E402
from refcheck.netaudit import DiskCache  # noqa: E402
from refcheck.pubmed import (NCBIBadResponse, NCBIConnectionError, NCBIOfflineMiss,  # noqa: E402
                             NCBIRateLimited, NCBITimeout, _norm_doi, decide,
                             match_reference, score_candidate)
from refcheck.extract import parse_reference_text  # noqa: E402

ROBIN = ("Deacon S, Cahyani I, et al. ROBIN: A unified nanopore-based assay integrating intraoperative "
         "methylome classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis. "
         "Neuro Oncol. 2025;27(8):2035-2046.")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    import refcheck.pubmed as pm
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)   # scoped to each test


def fast(c):
    return c


def test_retry_after_rate_limit_then_success():
    c = fast(MockClient(fail={"esearch.fcgi": [429, 503]}))
    assert c.esearch('"10.1093/neuonc/noaf103"[aid]') == ["40392954"]
    kinds = [r.status for r in c.audit.requests]
    assert kinds == ["rate_limited", "server_error", "ok"]


def test_error_kinds():
    c = fast(MockClient(fail={"esearch.fcgi": [requests.Timeout()] * 4}))
    with pytest.raises(NCBITimeout):
        c.esearch("x[Title]")
    c = fast(MockClient(fail={"esearch.fcgi": [requests.ConnectionError()] * 4}))
    with pytest.raises(NCBIConnectionError):
        c.esearch("x[Title]")
    c = fast(MockClient(fail={"esearch.fcgi": [429] * 4}))
    with pytest.raises(NCBIRateLimited):
        c.esearch("x[Title]")
    c = fast(MockClient(fail={"esearch.fcgi": [400]}))
    with pytest.raises(NCBIBadResponse):          # not retried
        c.esearch("x[Title]")
    assert len(c.audit.requests) == 1


def test_error_status_when_every_search_fails():
    c = fast(MockClient(fail={"esearch.fcgi": [requests.ConnectionError()] * 200,
                              "ecitmatch.cgi": [requests.ConnectionError()] * 200}))
    res = match_reference(c, parse_reference_text(ROBIN))
    assert res["status"] == "ERROR" and "connection" in res["error_kinds"]


def test_disk_cache_and_offline_mode(tmp_path):
    cache = DiskCache(str(tmp_path / "c.sqlite"))
    online = MockClient(cache=cache)
    ref = parse_reference_text(ROBIN)
    first = match_reference(online, ref)
    assert first["status"] == "AUTO_MATCH" and online.audit.summary()["network_requests"] > 0

    offline = MockClient(cache=cache, offline=True)
    again = match_reference(offline, ref)
    assert again["best"]["pmid"] == first["best"]["pmid"]
    assert offline.audit.summary()["network_requests"] == 0 and offline.calls == []
    with pytest.raises(NCBIOfflineMiss):
        offline.esearch("never asked before[Title]")


def test_cache_key_excludes_credentials(tmp_path):
    k1 = DiskCache.key("esearch.fcgi", {"term": "x", "email": "a@b.c", "api_key": "SECRET"})
    k2 = DiskCache.key("esearch.fcgi", {"term": "x", "email": "z@y.x"})
    assert k1 == k2
    cache = DiskCache(str(tmp_path / "c.sqlite"))
    cache.put("esearch.fcgi", {"term": "x", "api_key": "SECRET"}, "<x/>")
    assert b"SECRET" not in (tmp_path / "c.sqlite").read_bytes()


def test_audit_redacts_key_and_lists_fields():
    c = MockClient()
    c.api_key = "SECRET"
    c.esearch('"10.1093/neuonc/noaf103"[aid]')
    r = c.audit.requests[0]
    assert r.transmitted["api_key"] == "(redacted)" and r.transmitted["email"] == "(contact email)"
    assert "term" in c.audit.summary()["parameters_sent"]


def test_online_first_year_counts_as_match():
    arts = [dict(ARTICLES[2], year="2026", epub="2025")]     # print 2026, online 2025
    cand = MockClient(articles=arts).efetch(["40392954"])[0]
    sc, ts, to, why = score_candidate(parse_reference_text(ROBIN), cand)
    assert "year(online-first)" in why and "year-mismatch" not in why


def _cand(**kw):
    base = dict(pmid="1", score=100, title_similarity=0.99, match_reasons="title>=0.98;first-author;year",
                pubtypes=["Journal Article"])
    base.update(kw)
    return base


def test_abstention_rules():
    s = Settings(cache_path="")
    assert decide(_cand(), None, s) == ("AUTO_MATCH", [])
    st, why = decide(_cand(match_reasons="title>=0.98;author-differs;year"), None, s)
    assert st == "REVIEW" and "first author differs" in why
    st, why = decide(_cand(), _cand(pmid="2", score=97), s)
    assert st == "REVIEW" and "runner up" in why[0]
    # an erratum for the same paper is not a rival
    st, _ = decide(_cand(), _cand(pmid="2", score=99, pubtypes=["Published Erratum"]), s)
    assert st == "AUTO_MATCH"
    # identifier evidence overrides an author or year disagreement
    st, _ = decide(_cand(match_reasons="title>=0.98;author-differs;year-mismatch;doi"), None, s)
    assert st == "AUTO_MATCH"


def test_thresholds_configurable(tmp_path):
    cfg = tmp_path / "t.json"
    cfg.write_text('{"auto_score": 95, "review_score": 60}')
    s = load_settings(str(cfg), env={})
    assert s.auto_score == 95 and "t.json" in s.threshold_source
    assert decide(_cand(score=90), None, s)[0] == "REVIEW"
    s2 = load_settings(env={"REFCHECK_AUTO_SCORE": "80"})
    assert s2.auto_score == 80 and s2.threshold_source == "environment variables"
    assert "provisional" in Settings().threshold_source


def test_doi_normalisation():
    for d in ("https://doi.org/10.1038/S43018-025-00976-5.", "doi: 10.1038/s43018-025-00976-5",
              "http://dx.doi.org/10.1038/s43018-025-00976-5"):
        assert _norm_doi(d) == "10.1038/s43018-025-00976-5"


def test_identifier_hits_recorded():
    ref = parse_reference_text(ROBIN + " doi:10.1093/neuonc/noaf194")      # DOI of a different paper
    res = match_reference(MockClient(), ref)
    assert res["identifier_hits"]["doi"] == ["41092086"]
    assert res["best"]["pmid"] == "40392954"


def test_ablation_switches():
    ref = parse_reference_text(ROBIN)
    full = match_reference(MockClient(), ref)
    assert "citmatch" in full["methods_run"]
    s = Settings(cache_path="", disabled=["citmatch", "journal-vol-page", "author-year-page", "early-stop"])
    abl = match_reference(MockClient(settings=s), ref, settings=s)
    assert not {"citmatch", "journal-vol-page", "author-year-page"} & set(abl["methods_run"])
    assert "title-words" in abl["methods_run"]               # no early stop: every remaining step ran
    assert abl["best"]["pmid"] == full["best"]["pmid"]
    s2 = Settings(cache_path="", disabled=["abstention"])
    assert decide(_cand(match_reasons="title>=0.98;author-differs;year"), None, s2)[0] == "AUTO_MATCH"
    arts = [dict(ARTICLES[2], year="2026", epub="2025")]
    s3 = Settings(cache_path="", disabled=["online-first"])
    cand = MockClient(articles=arts).efetch(["40392954"])[0]
    assert "year-mismatch" not in score_candidate(ref, cand)[3]
    assert "year+-1" in score_candidate(ref, cand, s3)[3]
    assert load_settings(env={"REFCHECK_DISABLE": "doi, citmatch"}).disabled == ["doi", "citmatch"]
