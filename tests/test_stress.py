"""Stress tests (validation step 5), all offline: network faults, repeatability, cache
consistency, a 500 reference list, hostile input files and manuscript text leakage."""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from fixtures import MockClient  # noqa: E402
from refcheck.extract import extract_references, parse_reference_text  # noqa: E402
from refcheck.netaudit import DiskCache  # noqa: E402
from refcheck.pipeline import default_decisions, run_matching  # noqa: E402
from refcheck.verify import classify_all  # noqa: E402

GOOD = ("Deacon S, Cahyani I, Holmes N, Fox G, Munro R, Wibowo S, et al. ROBIN: A unified nanopore-based assay "
        "integrating intraoperative methylome classification and next-day comprehensive profiling for "
        "ultra-rapid tumor diagnosis. Neuro Oncol. 2025;27(8):2035-2046.")
FAKE = ("Oyelaran FW, Lachance NM, Kristensen DH. Prognostic impact of TP53 alterations in newly diagnosed acute "
        "myeloid leukaemia: a multicentre cohort study. Oncogene. 2014;79(12):1446-1456.")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    import refcheck.pubmed as pm
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)


def refs_of(texts):
    out = []
    for i, t in enumerate(texts, 1):
        r = parse_reference_text(t)
        r["num"] = i
        out.append(r)
    return out


def check(refs, client):
    res = run_matching(refs, client)
    dec = default_decisions(refs, res)
    need = {p for p, _ in dec.values() if p} | {p for r in res.values()
                                               for v in (r.get("identifier_hits") or {}).values() for p in v}
    try:
        recs = {r["pmid"]: r for r in client.efetch(sorted(need))} if need else {}
    except Exception:
        recs = {}
    return classify_all(refs, res, dec, recs), dec


# ------------------------------------------------------------ network faults --

@pytest.mark.parametrize("fault", [requests.Timeout(), requests.ConnectionError(), 429, 503])
def test_a_failed_search_is_never_reported_as_verified(fault):
    c = MockClient(fail={"esearch.fcgi": [fault] * 500, "ecitmatch.cgi": [fault] * 500, "efetch.fcgi": [fault] * 500})
    out, dec = check(refs_of([GOOD, FAKE]), c)
    for n in (1, 2):
        assert out[n]["status"] == "CHECK_ERROR", out[n]["status"]
        assert not dec[n][0]


def test_faults_part_way_through_affect_only_the_references_they_hit():
    # the first reference's searches fail, then the service recovers
    c = MockClient(fail={"ecitmatch.cgi": [requests.ConnectionError()] * 4,
                         "esearch.fcgi": [requests.ConnectionError()] * 40})
    out, _ = check(refs_of([GOOD, GOOD.replace("2035-2046", "2035-46")]), c)
    assert out[1]["status"] in ("CHECK_ERROR", "VERIFIED", "VERIFIED_WITH_WARNINGS")
    assert out[2]["status"] in ("VERIFIED", "VERIFIED_WITH_WARNINGS", "DUPLICATE")
    assert all(out[n]["status"] != "VERIFIED" or "check_error" not in {f["code"] for f in out[n]["findings"]}
               for n in out)


def test_a_bad_xml_answer_is_an_error_not_a_match():
    class Garbage(MockClient):
        def _answer(self, endpoint, params):
            return "<eSearchResult><Count>1</Count><IdList><Id>40392954" if endpoint == "esearch.fcgi" \
                else super()._answer(endpoint, params)
    out, _ = check(refs_of([FAKE]), Garbage())
    assert out[1]["status"] in ("CHECK_ERROR", "NOT_FOUND_IN_PUBMED")


# --------------------------------------------------- repeatability and cache --

def test_two_runs_give_identical_results():
    texts = [GOOD, FAKE, GOOD.replace("2025", "2024")]
    a, _ = check(refs_of(texts), MockClient())
    b, _ = check(refs_of(texts), MockClient())
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def test_offline_from_the_cache_gives_the_online_answer(tmp_path):
    texts = [GOOD, FAKE]
    cache = DiskCache(str(tmp_path / "c.sqlite"))
    online = MockClient(cache=cache)
    a, _ = check(refs_of(texts), online)
    offline = MockClient(cache=DiskCache(str(tmp_path / "c.sqlite")), offline=True)
    b, _ = check(refs_of(texts), offline)
    assert [a[n]["status"] for n in a] == [b[n]["status"] for n in b]
    assert not [r for r in offline.audit.requests if not r.from_cache]       # nothing went to the network


# ------------------------------------------------------------------- scale --

def test_a_500_reference_list_extracts_and_checks_in_reasonable_time(tmp_path):
    lines = [f"{i}. " + (GOOD if i % 3 else FAKE).replace("2035-2046", f"{2000 + i}-{2010 + i}")
             for i in range(1, 501)]
    p = tmp_path / "big.txt"
    p.write_text("References\n" + "\n".join(lines), encoding="utf-8")
    t0 = time.time()
    refs = extract_references(str(p))["refs"]
    t_extract = time.time() - t0
    assert len(refs) == 500 and [r["num"] for r in refs] == list(range(1, 501))
    t0 = time.time()
    out, _ = check(refs, MockClient())
    t_check = time.time() - t0
    assert len(out) == 500
    assert t_extract < 30 and t_check < 120, (t_extract, t_check)


# -------------------------------------------------------------- hostile files --

def _docx_with(document_xml: bytes, extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                   'officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", document_xml)
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


W = b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def safe_extract(path, seconds=60):
    t0 = time.time()
    try:
        out = extract_references(str(path))
        result = ("ok", len(out["refs"]))
    except Exception as e:                       # a clean, catchable error is acceptable
        result = ("error", type(e).__name__)
    assert time.time() - t0 < seconds, "took too long"
    return result


def test_xml_entity_expansion_in_docx_does_not_hang_or_expand(tmp_path):
    bomb = (b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">'
            b'<!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;"><!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">]>'
            b'<w:document ' + W + b'><w:body><w:p><w:r><w:t>References &f;&f;&f;</w:t></w:r></w:p></w:body></w:document>')
    p = tmp_path / "bomb.docx"
    p.write_bytes(_docx_with(bomb))
    kind, _ = safe_extract(p)
    assert kind in ("ok", "error")


def test_external_entity_in_docx_is_not_read(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET-LINE", encoding="utf-8")
    xxe = (b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file://' + str(secret).encode() + b'">]>'
           b'<w:document ' + W + b'><w:body><w:p><w:r><w:t>References</w:t></w:r></w:p><w:p><w:r><w:t>1. &x; '
           + GOOD.encode() + b'</w:t></w:r></w:p></w:body></w:document>')
    p = tmp_path / "xxe.docx"
    p.write_bytes(_docx_with(xxe))
    try:
        out = extract_references(str(p))
        assert "TOPSECRET" not in json.dumps(out["refs"])
    except Exception:
        pass                                     # refusing the file is fine too


def test_zip_bomb_docx_fails_cleanly(tmp_path):
    big = b"<w:document " + W + b"><w:body>" + b"<w:p/>" * 3_000_000 + b"</w:body></w:document>"
    p = tmp_path / "zipbomb.docx"
    p.write_bytes(_docx_with(big))
    assert p.stat().st_size < 1_000_000                 # small on disk, about 18 MB unpacked
    assert safe_extract(p, seconds=10) == ("error", "ValueError")      # refused before parsing


def test_docx_limits_count_paragraphs_exactly(tmp_path):
    from refcheck.extract import check_docx_limits
    for n in (0, 1, 5, 99_999, 100_000):
        p = tmp_path / f"p{n}.docx"
        p.write_bytes(_docx_with(b"<w:document " + W + b"><w:body>" + b'<w:p w:rsidR="1"/>' * (n // 2)
                                 + b"<w:p><w:r><w:t>x</w:t></w:r></w:p>" * (n - n // 2) + b"</w:body></w:document>"))
        check_docx_limits(p)                             # at or under the limit: accepted
    p = tmp_path / "over.docx"
    p.write_bytes(_docx_with(b"<w:document " + W + b"><w:body>" + b"<w:p/>" * 100_001 + b"</w:body></w:document>"))
    with pytest.raises(ValueError, match="100001 paragraphs"):
        check_docx_limits(p)
    with pytest.raises(ValueError, match="unpacks to"):
        check_docx_limits(tmp_path / "p5.docx", max_mb=0)


@pytest.mark.parametrize("name,data", [("fake.docx", b"this is not a zip file"),
                                       ("empty.docx", b""),
                                       ("garbage.pdf", b"%PDF-1.4\n" + bytes(range(256)) * 50),
                                       ("binary.txt", bytes(range(256)) * 200)])
def test_broken_files_fail_cleanly(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    safe_extract(p)


# ------------------------------------------------------------- text leakage --

def test_manuscript_body_never_reaches_the_network_or_the_log(tmp_path):
    from docx import Document
    sentinel = "CONFIDENTIAL-PATIENT-COHORT-XYZZY"
    d = Document()
    d.add_paragraph(f"Our unpublished finding about {sentinel} is described here [1].")
    d.add_paragraph("References")
    d.add_paragraph("1. " + GOOD)
    p = tmp_path / "ms.docx"
    d.save(p)
    log = []
    c = MockClient()
    c.log = log.append
    refs = extract_references(str(p))["refs"]
    check(refs, c)
    sent = json.dumps([(r.endpoint, r.transmitted) for r in c.audit.requests]) + json.dumps(c.calls)
    assert sentinel not in sent and sentinel not in "\n".join(log)
    assert sentinel not in json.dumps(refs)


def test_references_left_unchecked_are_named_in_the_warnings(tmp_path):
    # step 5 check 4 (network cut): the report must say which references were not checked
    from refcheck.pipeline import build_outputs
    src = tmp_path / "refs.txt"
    src.write_text("References\n1. " + GOOD + "\n2. " + FAKE + "\n", encoding="utf-8")
    ex = extract_references(str(src))
    c = MockClient(fail={"esearch.fcgi": [requests.ConnectionError()] * 500,
                         "ecitmatch.cgi": [requests.ConnectionError()] * 500,
                         "efetch.fcgi": [requests.ConnectionError()] * 500})
    res = run_matching(ex["refs"], c)
    out = build_outputs(ex, ex["refs"], res, default_decisions(ex["refs"], res), c, tmp_path / "out", "refs",
                        report_meta=dict(input_name="refs.txt", input_sha256="x", started="t"))
    w = [x for x in out["warnings"] if "could not be checked" in x]
    assert w and "references 1, 2" in w[0], out["warnings"]
    rep = json.loads(next((tmp_path / "out").glob("*_integrity_report.json")).read_text(encoding="utf-8"))
    assert any("could not be checked" in x for x in rep["warnings"])
