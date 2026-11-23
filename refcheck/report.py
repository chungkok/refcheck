"""RefReport: reproducible integrity report (JSON and self contained HTML).

Every finding carries its source. Automatic findings and user decisions are kept
apart (`decided_by`, `user_decision`). The report records software and algorithm
versions, thresholds and where they came from, timestamps, the input file's SHA 256
and the full network audit, so a run can be repeated and checked.
"""

from __future__ import annotations

import hashlib
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .export import vancouver
from . import __version__
from .styles import FAMILIES, detect_reference_style
from .verify import STATUS_HELP

ALGORITHM_VERSION = "match-2.0 / verify-2.0 / audit-1.0"

DISCLAIMERS = [
    "The match score is an evidence score, not a probability. Thresholds are "
    "provisional until calibrated on a labelled benchmark (see threshold_source).",
    "A reference that is not found in PubMed is not thereby fabricated. Books, guidelines, "
    "software, reports and many journals are outside PubMed.",
    "Secondary sources (Crossref, OpenAlex, doi.org, NLM Catalog, DOAJ, Unpaywall, Retraction Watch) "
    "are checked only when enabled. A source that could not be reached is listed in the warnings; "
    "its checks are missing, not passed. Retraction and correction notices are reported with the "
    "source that gave them and should be confirmed on the notice itself.",
    "Temporary citations in the Word copy are plain text until EndNote has imported the "
    "records and updated the document. Use the round trip check to confirm the result.",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pub_subset(rec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    keep = ("pmid", "pmcid", "title", "journal", "iso_journal", "year", "epub_year", "volume", "issue",
            "pages", "first_author", "authors", "doi", "pubtypes", "links")
    return {k: rec.get(k) for k in keep}


def build_report(*, input_name: str, input_sha256: str, started: str, refs: List[Dict[str, Any]],
                 results: Dict[int, Dict[str, Any]], outputs: Dict[str, Any], settings: Any,
                 network: Any, overrides: Optional[Dict[int, str]] = None,
                 reviewed: Optional[Dict[int, bool]] = None,
                 roundtrip: Optional[Dict[str, Any]] = None, deployment: str = "unknown") -> Dict[str, Any]:
    overrides, reviewed = overrides or {}, reviewed or {}
    decisions, records, integrity = outputs["decisions"], outputs["records"], outputs["integrity"]
    items = []
    for ref in refs:
        n = ref["num"]
        res = results.get(n, {})
        best, second = res.get("best"), res.get("second")
        pmid, dstatus = decisions.get(n, ("", ""))
        auto_pmid = best["pmid"] if (best and res.get("status") == "AUTO_MATCH") else ""
        user_changed = n in overrides and str(overrides[n] or "") != auto_pmid
        it = integrity[n]
        items.append(dict(
            ref_no=n,
            original=ref["text"],
            parsed={k: ref.get(k, "") for k in ("authors", "title", "journal", "year", "volume", "issue",
                                                  "pages", "doi", "pmid_hint")},
            parse=dict(method=ref.get("parse_method", ""), confidence=ref.get("confidence", {}),
                       note=ref.get("parse_note", "")),
            detected_style=_style(ref["text"]),
            automatic=dict(
                match_status=res.get("status", ""), score=best["score"] if best else None,
                score_kind="uncalibrated evidence score",
                best_pmid=best["pmid"] if best else "", reasons=(best or {}).get("match_reasons", ""),
                runner_up_pmid=second["pmid"] if second else "", runner_up_score=second["score"] if second else None,
                abstained_because=res.get("abstain") or [], identifier_hits=res.get("identifier_hits") or {},
                searches_run=res.get("methods_run") or [], error_kinds=res.get("error_kinds") or [],
                scoring=res.get("scoring", "points"),
                probability=(best or {}).get("probability"),
                weights_explained=(best or {}).get("weights_explained", ""),
                candidates=res.get("candidates") or [],
            ),
            user_decision=dict(reviewed=bool(reviewed.get(n)), changed_pmid=user_changed,
                               pmid=overrides.get(n) if user_changed else None),
            final=dict(pmid=pmid, decision_status=dstatus, decided_by=it["decided_by"],
                       endnote_record=outputs["ref_to_rec"].get(n)),
            integrity=dict(status=it["status"], meaning=it["help"], flags=it["flags"], findings=it["findings"]),
            pubmed=_pub_subset(records.get(pmid)) if pmid else None,
            suggested_reference=vancouver(records.get(pmid)) if pmid else "",
            open_access_url=((outputs.get("evidence") or {}).get(n) or {}).get("oa_url") or "",
            links=dict(pubmed=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                       doi=f"https://doi.org/{(records.get(pmid) or {}).get('doi') or ref.get('doi')}"
                       if ((records.get(pmid) or {}).get("doi") or ref.get("doi")) else ""),
        ))
    return dict(
        report_format="refcheck-report/1",
        software=dict(name="RefCheck", version=__version__, algorithm=ALGORITHM_VERSION,
                      python=platform.python_version()),
        run=dict(started=started, finished=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 deployment=deployment),
        input=dict(name=input_name, sha256=input_sha256, references=len(refs),
                   kind=outputs.get("source_kind", "")),
        settings=settings.public_dict() if hasattr(settings, "public_dict") else dict(settings or {}),
        summary=dict(integrity=outputs["integrity_summary"],
                     decided_by_user=sum(1 for i in items if i["final"]["decided_by"] == "user"),
                     endnote_records=outputs["n_records"]),
        secondary_sources=outputs.get("sources_used", []),
        retraction_watch_dataset=outputs.get("retraction_watch"),
        reference_style=outputs.get("reference_style"),
        citation_audit=outputs.get("audit"),
        citation_findings=outputs.get("audit_findings", []),
        word_changes=outputs.get("changes"),
        existing_citation_fields=outputs.get("existing_fields", {}),
        ris_problems=outputs.get("ris_problems", []),
        reading_warnings=outputs.get("reading_warnings", []),
        warnings=outputs.get("warnings", []),
        roundtrip=roundtrip,
        network=dict(summary=network.summary(), requests=network.to_list()) if network else None,
        disclaimers=DISCLAIMERS,
        references=items,
    )


def _style(text: str) -> Dict[str, str]:
    d = detect_reference_style(text)
    return dict(family=FAMILIES[d["family"]], style=d["style"], evidence=d["evidence"])


def write_json(report: Dict[str, Any], path) -> None:
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


# -------------------------------------------------------------------- HTML --

_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mute:#6e6e73;--line:#d2d2d7;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--info:#0969da}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#f2f2f7;--mute:#a1a1a6;--line:#3a3a3c;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--info:#58a6ff}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;margin:0 auto;max-width:1100px;padding:24px 16px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.mute{color:var(--mute)}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600}.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid currentColor;white-space:nowrap}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.info{color:var(--info)}
.wrap{overflow-x:auto}ul{margin:4px 0 4px 18px;padding:0}code{font-size:12px}
"""
_TONE = {"VERIFIED": "ok", "VERIFIED_WITH_WARNINGS": "ok", "METADATA_ERROR": "warn", "CORRECTED": "warn",
         "PREPRINT": "info", "UNRESOLVED": "warn", "NOT_FOUND_IN_PUBMED": "mute", "NON_PUBMED_SOURCE": "mute",
         "DUPLICATE": "warn", "DOI_MISMATCH": "bad", "PMID_MISMATCH": "bad", "RETRACTED": "bad",
         "EXPRESSION_OF_CONCERN": "bad", "CHECK_ERROR": "bad"}


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def write_html(report: Dict[str, Any], path) -> None:
    r = report
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
         f"content='width=device-width,initial-scale=1'><title>RefCheck report</title><style>{_CSS}</style></head><body>"]
    h.append(f"<h1>RefCheck integrity report</h1><div class='mute'>{_e(r['input']['name'])} · "
             f"{r['input']['references']} references · generated {_e(r['run']['finished'])} · RefCheck "
             f"{_e(r['software']['version'])} ({_e(r['software']['algorithm'])})</div>")
    h.append("<h2>Summary</h2><table><tr><th>Integrity status</th><th>References</th><th>Meaning</th></tr>")
    for st, n in r["summary"]["integrity"].items():
        h.append(f"<tr><td><span class='pill {_TONE.get(st, '')}'>{_e(st)}</span></td><td>{n}</td>"
                 f"<td>{_e(STATUS_HELP.get(st, ''))}</td></tr>")
    h.append(f"</table><p class='mute'>Decided or confirmed by the user: {r['summary']['decided_by_user']}. "
             f"EndNote records: {r['summary']['endnote_records']}. Thresholds: {_e(r['settings'].get('threshold_source'))}. "
             f"Sources: PubMed{', ' + _e(', '.join(r['secondary_sources'])) if r.get('secondary_sources') else ' only'}.</p>")
    rwd = r.get("retraction_watch_dataset") or {}
    if rwd:
        h.append("<p class='mute'>Retraction Watch dataset: " + _e(rwd.get("status", "")) +
                 (f", {_e(rwd['rows'])} records" if rwd.get("rows") else "") +
                 (f", downloaded {_e(rwd['downloaded_at'])}" if rwd.get("downloaded_at") else "") +
                 (f", SHA-256 {_e(rwd['sha256'][:12])}" if rwd.get("sha256") else "") +
                 (f". {_e(rwd['message'])}" if rwd.get("message") else "") + ".</p>")
    rs = r.get("reference_style") or {}
    if rs:
        cnt = ", ".join(f"{_e(k)} {v}" for k, v in (rs.get("counts") or {}).items())
        h.append("<h2>Citation style</h2><p" + (" class='warn'" if rs.get("mixed") else "") + ">" +
                 (f"Detected style: {_e(rs['family_label'])}" + (f", best guess {_e(rs['style_guess'])}"
                                                                    if rs.get("style_guess") else "") +
                  f" ({rs['detected']} of {rs['references']} references recognised: {cnt})."
                  if rs.get("family") else "No citation style recognised.") +
                 (f" {_e(rs['message'])}" if rs.get("mixed") else "") + "</p>"
                 "<p class='mute'>Style detection is for information. Several journal styles are identical "
                 "for one reference, so the exact style is a best guess.</p>")
    if r.get("citation_findings"):
        h.append("<h2>Citation audit</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in r["citation_findings"]) + "</ul>")
    if r.get("warnings") or r.get("reading_warnings"):
        h.append("<h2>Warnings</h2><ul>" + "".join(f"<li>{_e(x)}</li>"
                                                   for x in (r.get("reading_warnings") or []) + r["warnings"]) + "</ul>")

    h.append("<h2>References</h2><div class='wrap'><table><tr><th>#</th><th>Status</th><th>Reference as written</th>"
             "<th>PubMed</th><th>Findings</th><th>Decided by</th></tr>")
    for it in r["references"]:
        st = it["integrity"]["status"]
        pm = it["final"]["pmid"]
        link = f"<a href='{_e(it['links']['pubmed'])}'>PMID {_e(pm)}</a>" if pm else "<span class='mute'>none</span>"
        if it["pubmed"]:
            link += f"<div class='mute'>{_e(it['pubmed']['title'])}</div>"
        if it["pubmed"] and it["pubmed"].get("authors") and any(
                f["code"] in ("authors_not_in_record", "authors_omitted", "author_order_differs",
                              "first_author_differs", "first_author_initials_differ") for f in it["integrity"]["findings"]):
            link += f"<div class='mute'>PubMed authors: {_e(it['pubmed']['authors'])}</div>"
        if it.get("suggested_reference"):
            link += f"<div class='mute'>Vancouver: {_e(it['suggested_reference'])}</div>"
        oa = it.get("open_access_url") or ""
        if oa.startswith(("https://", "http://")):
            link += f"<div><a href='{_e(oa)}'>open access copy</a></div>"
        fnd = "".join(f"<li><b>{_e(f['code'])}</b> {_e(f['detail'])} <span class='mute'>({_e(f['source'])})</span></li>"
                      for f in it["integrity"]["findings"])
        auto = it["automatic"]
        why = ""
        if auto["score"] is not None:
            why = f"<div class='mute'>score {auto['score']} ({_e(auto['score_kind'])})"
            if auto["abstained_because"]:
                why += "; abstained: " + _e("; ".join(auto["abstained_because"]))
            why += "</div>"
        ds = it.get("detected_style") or {}
        sty = (f"<div class='mute'>style: {_e(ds.get('style') or ds.get('family'))}</div>"
               if ds.get("family") and ds.get("family") != FAMILIES["unknown"] else "")
        h.append(f"<tr><td>{it['ref_no']}</td><td><span class='pill {_TONE.get(st, '')}'>{_e(st)}</span></td>"
                 f"<td>{_e(it['original'])}{sty}</td><td>{link}{why}</td><td><ul>{fnd}</ul></td>"
                 f"<td>{_e(it['final']['decided_by'])}</td></tr>")
    h.append("</table></div>")

    ch = r.get("word_changes")
    if ch:
        h.append(f"<h2>Changes made to the Word copy</h2><p>{ch['n_changed']} citations converted, "
                 f"{ch['n_unchanged']} left unchanged.</p>")
        if ch.get("structure"):
            h.append("<table><tr><th>Item</th><th>Original</th><th>Output</th><th>Expected</th><th></th></tr>")
            for s in ch["structure"]:
                h.append(f"<tr><td>{_e(s['item'])}</td><td>{s['original']}</td><td>{s['output']}</td>"
                         f"<td>{s['expected']}</td><td class='{'ok' if s['preserved'] else 'bad'}'>"
                         f"{'preserved' if s['preserved'] else 'changed'}</td></tr>")
            h.append("</table>")
        h.append("<div class='wrap'><table><tr><th>Where</th><th>Before</th><th>After</th><th>Note</th></tr>")
        for c in ch["changes"]:
            h.append(f"<tr><td>{_e(c['location'])} {_e(c['paragraph'])}</td><td><code>{_e(c['before'])}</code></td>"
                     f"<td><code>{_e(c['after'])}</code></td><td class='mute'>{_e(c['reason'])}</td></tr>")
        h.append("</table></div>")

    rt = r.get("roundtrip")
    if rt:
        h.append(f"<h2>EndNote round trip</h2><p class='{'ok' if rt['ok'] else 'bad'}'>"
                 f"{'All citations resolved as expected.' if rt['ok'] else 'Problems found.'}</p>"
                 f"<p>EndNote citation fields: {rt['endnote_citation_fields']}, expected: {rt['expected_citations']}, "
                 f"leftover temporary citations: {len(rt['leftover_temporary_citations'])}.</p>")
        for p in rt.get("problems", []):
            h.append(f"<div class='bad'>{_e(p)}</div>")

    net = r.get("network")
    if net:
        s = net["summary"]
        h.append(f"<h2>Data sent over the network</h2><p>{s['network_requests']} requests to {_e(s['destination'])}, "
                 f"{s['answered_from_cache']} answered from the local cache. Parameters sent: "
                 f"{_e(', '.join(s['parameters_sent']))}. Only search terms built from the parsed reference list "
                 f"were sent; no text from the body of the manuscript.</p>")
    h.append("<h2>Notes</h2><ul>" + "".join(f"<li>{_e(d)}</li>" for d in r["disclaimers"]) + "</ul>")
    h.append(f"<p class='mute'>Input SHA 256 {_e(r['input']['sha256'])}</p></body></html>")
    Path(path).write_text("".join(h), encoding="utf-8")
