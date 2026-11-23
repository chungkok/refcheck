"""Phase 4: what leaves the computer, temporary data lifecycle, safe names."""
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from fixtures import MockClient  # noqa: E402
from make_manuscript import REFS, build  # noqa: E402
from refcheck.extract import extract_references  # noqa: E402
from refcheck.pipeline import build_outputs, default_decisions, run_matching  # noqa: E402
from refcheck.privacy import detect_deployment, pending_temp_folders, safe_stem, workspace  # noqa: E402

BODY_SENTENCES = ["Brain tumours are common", "Nanopore methods are fast", "Intraoperative classification",
                  "cells per well", "Preprints exist", "Workflow adapted from", "crossNN [5]", "Test manuscript"]
QUERY_SYNTAX = {"and", "title", "aid", "ta", "dp", "vi", "pg", "1au", "author", "k"}


def _tokens(s):
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t}


def test_only_reference_list_terms_are_transmitted(tmp_path):
    src = tmp_path / "ms.docx"
    build(src)
    ex = extract_references(src)
    refs = ex["refs"]
    client = MockClient()
    res = run_matching(refs, client)
    out = build_outputs(ex, refs, res, default_decisions(refs, res), client, tmp_path / "out", "ms",
                        report_meta=dict(input_name="ms.docx", input_sha256="x", started="t"))
    sent = client.audit.transmitted_values()
    assert sent, "the run should have made requests"
    blob = "\n".join(sent)
    for s in BODY_SENTENCES:
        assert s.lower() not in blob.lower(), s
    allowed = _tokens(" ".join(REFS)) | QUERY_SYNTAX
    for v in sent:
        extra = {t for t in _tokens(v) if not t.isdigit()} - allowed
        assert not extra, (v, extra)
    # the report records the audit without the email or key
    rep = json.loads(out["paths"]["json"].read_text())
    assert rep["network"]["summary"]["network_requests"] == client.audit.summary()["network_requests"] > 0
    assert "test@example.org" not in out["paths"]["json"].read_text()


def test_workspace_is_removed_even_after_an_error():
    seen = {}
    with pytest.raises(RuntimeError):
        with workspace({"a.docx": b"secret"}) as d:
            seen["d"] = d
            assert (d / "a.docx").read_bytes() == b"secret"
            raise RuntimeError("boom")
    from refcheck.privacy import _PENDING
    assert not seen["d"].exists() and str(seen["d"]) not in _PENDING


def test_workspace_cannot_write_outside_itself():
    with workspace({"../../escape.docx": b"x"}) as d:
        assert (d / "escape.docx").exists()
        assert not (d.parent.parent / "escape.docx").exists()


@pytest.mark.parametrize("name,expected", [
    ("../../etc/passwd", "passwd"), ("C:\\Users\\Ed\\My Paper (v2).docx", "My_Paper_v2"),
    ("", "manuscript"), ("....", "manuscript"), ("résumé final.docx", "résumé_final")])
def test_safe_stem(name, expected):
    assert safe_stem(name) == expected


def test_deployment_detection():
    assert detect_deployment("auto", "localhost:8501") == "local"
    assert detect_deployment("auto", "127.0.0.1:8501") == "local"
    assert detect_deployment("auto", "[::1]:8501") == "local"
    assert detect_deployment("auto", "0.0.0.0:8501") == "hosted"
    assert detect_deployment("auto", "refcheck.streamlit.app") == "hosted"
    assert detect_deployment("auto", "") == "hosted"            # unknown: cautious answer
    assert detect_deployment("local", "refcheck.streamlit.app") == "local"


def test_settings_report_hides_api_key():
    from refcheck.config import Settings
    assert Settings(ncbi_api_key="SECRET").public_dict()["ncbi_api_key"] == "set"


def test_streamlit_config_disables_usage_statistics():
    cfg = (HERE.parent / ".streamlit" / "config.toml").read_text()
    assert re.search(r"gatherUsageStats\s*=\s*false", cfg)
    assert re.search(r"maxUploadSize\s*=\s*\d+", cfg)


def test_xml_with_entities_is_refused():
    from refcheck import safexml
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><x>&b;</x>'
    with pytest.raises(safexml.UnsafeXML):
        safexml.fromstring(bomb)
    from refcheck.roundtrip import _cites_from_xml
    assert _cites_from_xml("<EndNote><!DOCTYPE x>") [0]["error"]


def test_safe_xml_fallback_without_defusedxml(monkeypatch):
    from refcheck import safexml
    monkeypatch.setattr(safexml, "_defused_fromstring", None)
    assert safexml.fromstring("<a><b>x</b></a>").find("b").text == "x"
    with pytest.raises(safexml.UnsafeXML):
        safexml.fromstring('<!DOCTYPE a SYSTEM "file:///etc/passwd"><a/>')


def test_safe_xml_accepts_real_ncbi_answers_and_refuses_attacks(monkeypatch):
    """Every NCBI E-utilities answer opens with a DOCTYPE naming a web DTD. RefCheck 2.0 dev
    refused these and so every live PubMed check failed; the mock PubMed now sends them too."""
    from refcheck import safexml
    real = ['<?xml version="1.0" encoding="UTF-8" ?>\n<!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD esearch 20060628//EN" '
            '"https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20060628/esearch.dtd">\n<eSearchResult><Count>1</Count>'
            '<IdList><Id>23634996</Id></IdList></eSearchResult>',
            '<?xml version="1.0" ?>\n<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" '
            '"https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">\n<PubmedArticleSet/>',
            '<!DOCTYPE eLinkResult SYSTEM "http://www.ncbi.nlm.nih.gov/entrez/query/DTD/eLink_101123.dtd"><eLinkResult/>']
    attacks = ['<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>',
               '<!DOCTYPE a [ ]><a/>',
               '<!DOCTYPE a SYSTEM "file:///etc/passwd"><a/>',
               '<!DOCTYPE a SYSTEM "secret.dtd"><a/>',
               '<!DOCTYPE a PUBLIC "x" "file:///etc/passwd"><a/>']
    for defused in (True, False):
        if not defused:
            monkeypatch.setattr(safexml, "_defused_fromstring", None)
        assert safexml.fromstring(real[0]).findtext("./IdList/Id") == "23634996"
        for r in real[1:]:
            safexml.fromstring(r)
        for x in attacks:
            with pytest.raises(safexml.UnsafeXML):
                safexml.fromstring(x)
