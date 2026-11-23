"""Round trip validation: did EndNote resolve the temporary citations correctly?

The user imports the RIS file into EndNote, opens the Word copy, runs
"Update Citations and Bibliography", saves, and uploads that final document here.
This module reads the EndNote fields EndNote wrote, identifies the records each
citation points to (by Label, which RefCheck sets to the manuscript reference
number, then PMID, then title), and compares them with what RefCheck inserted.

RefCheck never edits an EndNote library and never drives EndNote itself. This check
only reads the document EndNote produced.
"""

from __future__ import annotations

import base64
import re
from . import safexml as ET
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from docx.oxml.ns import qn

from .annotate import _note_parts, _part_element
from .textutil import similarity

TEMP_CIT_RE = re.compile(r"\{[^{}\n]{1,200}?#\d+[^{}\n]{0,200}\}|\{[A-Z][^{}\n#]{0,80},\s*(?:19|20)\d{2}[a-z]?\}")


def _fields(body) -> List[Dict[str, Any]]:
    """Complex fields in document order: instruction text and any fldData."""
    out, stack = [], []
    for el in body.iter():
        if el.tag == qn("w:fldChar"):
            t = el.get(qn("w:fldCharType"))
            if t == "begin":
                data = el.find(qn("w:fldData"))
                stack.append(dict(instr="", data=(data.text or "") if data is not None else ""))
            elif t == "end" and stack:
                f = stack.pop()
                f["nested"] = bool(stack)          # inside another field (EndNote's EN.CITE.DATA)
                out.append(f)
        elif el.tag == qn("w:instrText") and stack:
            stack[-1]["instr"] += el.text or ""
    for el in body.iter(qn("w:fldSimple")):
        out.append(dict(instr=el.get(qn("w:instr")) or "", data=""))
    return out


def _cites_from_xml(xml: str) -> List[Dict[str, str]]:
    xml = xml.strip()
    start = xml.find("<EndNote")
    if start < 0:
        return []
    end = xml.rfind("</EndNote>")
    # EndNote pads the base64 data of EN.CITE.DATA with NUL bytes after </EndNote>
    xml = xml[start:end + len("</EndNote>")] if end > start else xml[start:].replace("\x00", "")
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, ET.UnsafeXML):
        return [dict(error="unreadable EndNote citation XML")]
    cites = []
    for c in root.iter("Cite"):
        rec = c.find("record")
        get = lambda path: "".join(rec.find(path).itertext()).strip() if rec is not None and rec.find(path) is not None else ""
        cites.append(dict(
            author=(c.findtext("Author") or "").strip(), year=(c.findtext("Year") or "").strip(),
            recnum=(c.findtext("RecNum") or get("rec-number")).strip(),
            label=get("label"), pmid=get("accession-num"), title=get("titles/title"),
        ))
    return cites


def _decode(data: str) -> str:
    if not data:
        return ""
    try:
        return base64.b64decode(re.sub(r"\s+", "", data)).decode("utf-8", "replace")
    except Exception:
        return ""


def endnote_citations(path) -> Tuple[List[List[Dict[str, str]]], Dict[str, int], List[str]]:
    """(citations as lists of cites, counts, leftover temporary citations)."""
    doc = Document(str(path))
    roots = [doc.element.body] + [_part_element(part) for _, part in _note_parts(doc)]
    cits, counts = [], Counter()
    pending = ""            # XML of a nested EN.CITE.DATA field, for the EN.CITE field around it
    for f in (f for root in roots for f in _fields(root)):
        ins = f["instr"].strip()
        if "EN.REFLIST" in ins:
            counts["bibliography"] += 1
            continue
        if "EN.CITE" not in ins:
            continue
        if "EN.CITE.DATA" in ins and f.get("nested"):
            # the inner field of a long citation: EndNote keeps its XML base64 encoded in fldData.
            # It is read with the EN.CITE field around it (fields end inner first), never on its own.
            pending = _decode(f["data"]) or pending
            continue
        if "EN.CITE.DATA" in ins:                # a data field standing alone is one citation
            cits.append(_cites_from_xml(_decode(f["data"])))
            counts["citation_fields"] += 1
            continue
        # one citation: XML in the instruction text, else in its own fldData, else in the nested field
        xml = ins if "<EndNote" in ins else (_decode(f["data"]) or pending)
        pending = ""
        cits.append(_cites_from_xml(xml))
        counts["citation_fields"] += 1
    # leftover temporary citations in plain text
    text = "\n".join("".join(t.text or "" for t in p.iter(qn("w:t")))
                     for root in roots for p in root.iter(qn("w:p")))
    leftovers = TEMP_CIT_RE.findall(text)
    counts["leftover_temporary"] = len(leftovers)
    return cits, dict(counts), leftovers


def _ref_for_cite(cite, refs_by_num, pmid_to_refs, records_by_ref):
    lab = re.findall(r"\d+", cite.get("label", ""))
    if lab:
        return [int(x) for x in lab], "label"
    if cite.get("pmid") and cite["pmid"] in pmid_to_refs:
        return pmid_to_refs[cite["pmid"]], "pmid"
    t = cite.get("title", "")
    if t:
        best = max(records_by_ref.items(), key=lambda kv: similarity(t, kv[1]), default=(None, ""))
        if best[0] is not None and similarity(t, best[1]) >= 0.9:
            return [best[0]], "title"
    return [], "unidentified"


def validate_roundtrip(final_docx, refs: List[Dict[str, Any]], citation_log: List[Dict[str, Any]],
                       decisions: Dict[int, Tuple[str, str]], records: Optional[Dict[str, Dict]] = None,
                       preexisting: int = 0) -> Dict[str, Any]:
    """Compare EndNote's citations in the final document with RefCheck's conversions.

    preexisting: number of EndNote citation fields already in the original manuscript
    (RefCheck left them alone, so they are not counted as unexpected)."""
    records = records or {}
    cits, counts, leftovers = endnote_citations(final_docx)
    refs_by_num = {r["num"]: r for r in refs}
    pmid_to_refs: Dict[str, List[int]] = {}
    for n, (pm, _) in decisions.items():
        if pm:
            pmid_to_refs.setdefault(pm, []).append(n)
    titles = {n: (records.get(decisions.get(n, ("", ""))[0], {}).get("title") or r.get("title", ""))
              for n, r in refs_by_num.items()}

    # what RefCheck inserted, as a multiset of sorted reference sets
    expected = Counter(tuple(sorted(set(int(x) for x in e["refs"].split(",") if x)))
                       for e in citation_log if e.get("replaced_with"))
    found, problems = Counter(), []
    for i, cites in enumerate(cits, 1):
        nums, how = set(), set()
        for c in cites:
            if "error" in c:
                problems.append(f"EndNote citation {i}: {c['error']}")
                continue
            got, method = _ref_for_cite(c, refs_by_num, pmid_to_refs, titles)
            how.add(method)
            if not got:
                problems.append(f"EndNote citation {i}: record {c.get('recnum')} "
                                f"('{c.get('author')}, {c.get('year')}') does not match any manuscript reference")
            nums.update(got)
        found[tuple(sorted(nums))] += 1

    # a reference shared by duplicate entries collapses to one record: compare by record
    def canon(key):
        pm = {decisions.get(n, ("", ""))[0] or f"ref{n}" for n in key}
        return tuple(sorted(pm))
    exp_c = Counter()
    for k, v in expected.items():
        exp_c[canon(k)] += v
    got_c = Counter()
    for k, v in found.items():
        got_c[canon(k)] += v
    missing = exp_c - got_c
    extra = got_c - exp_c
    allowance = preexisting
    for k in list(extra):
        while allowance and extra[k]:
            extra[k] -= 1
            allowance -= 1
    extra = +extra
    if preexisting:
        problems = problems[: max(0, len(problems) - (preexisting - allowance))] if problems else problems
    ok = not leftovers and not missing and not extra and not problems and counts.get("citation_fields", 0) > 0
    return dict(
        ok=ok,
        endnote_citation_fields=counts.get("citation_fields", 0),
        bibliography_fields=counts.get("bibliography", 0),
        expected_citations=sum(expected.values()),
        leftover_temporary_citations=leftovers,
        citations_missing=[dict(records=list(k), count=v) for k, v in missing.items()],
        citations_unexpected=[dict(records=list(k), count=v) for k, v in extra.items()],
        problems=problems,
        preexisting_fields_ignored=preexisting - allowance,
    )
