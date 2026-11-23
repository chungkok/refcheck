"""GROBID TEI parsing (offline) and the remote server guard."""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from refcheck.grobid import GrobidError, grobid_references, parse_tei  # noqa: E402

TEI = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back><div><listBibl>
<biblStruct xml:id="b0"><analytic><title level="a" type="main">ROBIN: A unified nanopore-based assay</title>
<author><persName><forename type="first">Simon</forename><surname>Deacon</surname></persName></author>
<author><persName><forename type="first">Inswasti</forename><surname>Cahyani</surname></persName></author>
<idno type="DOI">10.1093/neuonc/noaf103</idno></analytic>
<monogr><title level="j">Neuro Oncol</title><imprint><biblScope unit="volume">27</biblScope>
<biblScope unit="issue">8</biblScope><biblScope unit="page" from="2035" to="2046"/>
<date type="published" when="2025"/></imprint></monogr>
<note type="raw_reference">Deacon S, Cahyani I. ROBIN: A unified nanopore-based assay. Neuro Oncol. 2025;27(8):2035-2046.</note>
</biblStruct></listBibl></div></back></text></TEI>"""


def test_parse_tei():
    (r,) = parse_tei(TEI)
    assert (r["authors"], r["journal"], r["year"], r["volume"], r["issue"], r["pages"], r["doi"]) == (
        "Deacon S, Cahyani I", "Neuro Oncol", "2025", "27", "8", "2035-2046", "10.1093/neuonc/noaf103")
    assert r["title"].startswith("ROBIN") and r["parse_method"] == "grobid" and r["text"].startswith("Deacon")


def test_remote_server_refused(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(GrobidError, match="not on this computer"):
        grobid_references(pdf, "https://grobid.example.org")
