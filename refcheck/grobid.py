"""Optional PDF reference extraction with a local GROBID server.

GROBID (https://github.com/grobidOrg/grobid, Apache 2.0) is a separate program.
Start it locally, for example:
    docker run --rm -p 127.0.0.1:8070:8070 grobid/grobid:0.8.1
then run  refcheck_cli.py paper.pdf --grobid http://localhost:8070 ...

The PDF is posted to that server. RefCheck refuses a server that is not on this
computer unless allow_remote=True, so a manuscript is not sent elsewhere by accident.
Without GROBID, RefCheck reads the PDF text layer itself (pypdf).
"""

from __future__ import annotations

from . import safexml as ET
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

TEI = "{http://www.tei-c.org/ns/1.0}"
LOCAL = {"localhost", "127.0.0.1", "::1"}


class GrobidError(RuntimeError):
    pass


def _t(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_tei(tei_xml: str) -> List[Dict[str, Any]]:
    """GROBID TEI <listBibl> -> RefCheck reference dicts (same keys as the text parser)."""
    from .extract import _blank, field_confidence
    root = ET.fromstring(tei_xml)
    out = []
    for i, b in enumerate(root.iter(f"{TEI}biblStruct"), 1):
        rec = _blank()
        an, mo = b.find(f"{TEI}analytic"), b.find(f"{TEI}monogr")
        src = an if an is not None else mo
        names = []
        for au in (src.findall(f"{TEI}author") if src is not None else []):
            sn = _t(au.find(f".//{TEI}surname"))
            fn = "".join(x.text[0] for x in au.findall(f".//{TEI}forename") if x.text)
            if sn:
                names.append(f"{sn} {fn}".strip())
        rec["authors"] = ", ".join(names)
        rec["title"] = _t(an.find(f"{TEI}title")) if an is not None else _t(mo.find(f"{TEI}title"))
        if mo is not None and an is not None:
            jt = mo.find(f"{TEI}title[@level='j']")
            if jt is None:
                jt = mo.find(f"{TEI}title")
            rec["journal"] = _t(jt)
        imp = mo.find(f"{TEI}imprint") if mo is not None else None
        if imp is not None:
            d = imp.find(f"{TEI}date")
            if d is not None:
                rec["year"] = (d.get("when") or _t(d))[:4]
            for sc in imp.findall(f"{TEI}biblScope"):
                unit = sc.get("unit")
                if unit == "volume":
                    rec["volume"] = sc.get("from") or _t(sc)
                elif unit == "issue":
                    rec["issue"] = sc.get("from") or _t(sc)
                elif unit == "page":
                    a, z = sc.get("from"), sc.get("to")
                    rec["pages"] = f"{a}-{z}" if a and z else (a or _t(sc))
        for idno in b.iter(f"{TEI}idno"):
            kind = (idno.get("type") or "").upper()
            if kind == "DOI":
                rec["doi"] = _t(idno)
            elif kind == "PMID":
                rec["pmid_hint"] = _t(idno)
        raw = b.find(f"{TEI}note[@type='raw_reference']")
        rec["text"] = _t(raw) or ". ".join(x for x in (rec["authors"], rec["title"], rec["journal"], rec["year"]) if x)
        rec["num"] = i
        rec["parse_method"] = "grobid"
        rec["confidence"] = {k: ("missing" if not rec[k] else "medium")
                             for k in ("authors", "title", "journal", "year", "volume", "issue", "pages", "doi")}
        out.append(rec)
    return out


def grobid_references(pdf_path, url: str = "http://localhost:8070", timeout: float = 120,
                      allow_remote: bool = False) -> List[Dict[str, Any]]:
    import requests
    host = (urlparse(url).hostname or "").lower()
    if host not in LOCAL and not allow_remote:
        raise GrobidError(f"GROBID server {host!r} is not on this computer; refusing to send the PDF. "
                          "Pass allow_remote=True (command line: --grobid-allow-remote) only for a server "
                          "you control.")
    with open(Path(pdf_path), "rb") as fh:
        try:
            r = requests.post(url.rstrip("/") + "/api/processReferences",
                              files={"input": fh}, data={"consolidateCitations": "0", "includeRawCitations": "1"},
                              timeout=timeout)
        except requests.RequestException as e:
            raise GrobidError(f"cannot reach GROBID at {url}: {type(e).__name__}") from e
    if r.status_code != 200:
        raise GrobidError(f"GROBID answered HTTP {r.status_code}")
    return parse_tei(r.text)
