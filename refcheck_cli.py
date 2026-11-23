#!/usr/bin/env python3
"""Command line version of RefCheck.

Examples
    python refcheck_cli.py manuscript.docx --email you@example.org
    python refcheck_cli.py refs.pdf --email you@example.org --outdir results
    python refcheck_cli.py manuscript.docx --email you@x.org --accept-review --paren
    python refcheck_cli.py manuscript.docx --email you@x.org --pmid 6=12345678 --pmid 9=
    python refcheck_cli.py papers/ another.docx --email you@x.org --outdir results   (batch)
    python refcheck_cli.py manuscript.docx --email you@x.org --offline              (cache only)
    python refcheck_cli.py --roundtrip results/ms_integrity_report.json final.docx

Set NCBI_EMAIL / NCBI_API_KEY in the environment to skip the flags. Settings such as
thresholds can come from a JSON file (--config or REFCHECK_CONFIG).
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from refcheck.config import load_settings
from refcheck.extract import extract_references
from refcheck.pipeline import apply_overrides, build_outputs, default_decisions, run_matching
from refcheck.privacy import safe_stem, workspace
from refcheck.pubmed import PubMedClient
from refcheck.sources import ALL_SOURCES, evidence_from_settings

SUPPORTED = {".docx", ".doc", ".odt", ".rtf", ".pdf", ".txt", ".md", ".csv", ".tsv", ".xlsx",
             ".bib", ".bbl", ".tex"}


def expand_inputs(items):
    out = []
    for it in items:
        p = Path(it)
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.suffix.lower() in SUPPORTED and not f.name.startswith("~$"))
        else:
            out.append(p)
    return out


def run_one(src, a, settings, client, evidence=None):
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with workspace() as tmp:
        ex = extract_references(src, a.heading, workdir=tmp, grobid_url=a.grobid,
                                grobid_allow_remote=a.grobid_allow_remote)
        refs = ex["refs"]
        for w in ex["warnings"]:
            print("warning:", w)
        print(f"{len(refs)} references found in {src.name}")

        def prog(i, n, ref, res):
            b = res.get("best")
            tag = f"PMID {b['pmid']} score {b['score']}" if b else ""
            print(f"[{i:>3}/{n}] ref {ref['num']:>3}  {res['status']:<10} {tag}  {ref['title'][:60]}")

        results = run_matching(refs, client, prog)
        decisions = default_decisions(refs, results, a.accept_review)
        overrides = {}
        for spec in a.pmid:
            k, _, v = spec.partition("=")
            overrides[int(k)] = v
        decisions = apply_overrides(decisions, results, overrides)

        glued = a.glued == "on"
        if a.glued == "auto" and ex.get("docx_path") and ex.get("heading_para") is not None:
            from refcheck.annotate import detect_citation_forms, suggested_options
            counts = detect_citation_forms(ex["docx_path"], ex["heading_para"], len(refs))
            glued = suggested_options(counts)["glued"]
            print("citation forms found:", counts, "-> numbers attached to words", "on" if glued else "off")
        out = build_outputs(
            ex, refs, results, decisions, client, Path(a.outdir or src.parent), safe_stem(src.name),
            record_start=a.record_start, remove_reference_list=not a.keep_reference_list,
            annotate=not a.audit_only, evidence=evidence,
            report_meta=dict(input_name=src.name, input_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
                             started=started, overrides={k: v for k, v in overrides.items()},
                             deployment="local (command line)"),
            annotate_opts=dict(brackets=not a.no_brackets, superscript=not a.no_superscript,
                               parentheses=a.paren, glued=glued,
                               use_record_numbers=not a.no_record_numbers))
    for w in out["warnings"]:
        print("warning:", w)
    print("\nMatch summary:", ", ".join(f"{k} {v}" for k, v in sorted(
        _count(st for _, st in decisions.values()).items())))
    print("Integrity:", ", ".join(f"{k} {v}" for k, v in out["integrity_summary"].items()))
    for line in out.get("audit_findings", []):
        print("audit:", line)
    s = client.audit.summary()
    dest = ", ".join(f"{h} {n}" for h, n in s["by_destination"].items()) or "none"
    print(f"Network: {s['network_requests']} requests ({dest}), {s['answered_from_cache']} from cache, "
          f"{s['errors']} failed. Only bibliographic search terms and identifiers were sent.")
    for k, p in out["paths"].items():
        print(f"  {k:11s} {p}")
    return out


def _count(xs):
    c = {}
    for x in xs:
        c[x] = c.get(x, 0) + 1
    return c


def roundtrip(report_path, final_docx):
    from refcheck.roundtrip import validate_roundtrip
    rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
    changes = (rep.get("word_changes") or {}).get("changes") or []
    log = [dict(replaced_with=c["after"] if c["changed"] else "", refs=c["references"]) for c in changes]
    refs = [dict(num=r["ref_no"], title=r["parsed"]["title"]) for r in rep["references"]]
    decisions = {r["ref_no"]: (r["final"]["pmid"], r["final"]["decision_status"]) for r in rep["references"]}
    records = {r["final"]["pmid"]: r["pubmed"] for r in rep["references"] if r["final"]["pmid"] and r["pubmed"]}
    rt = validate_roundtrip(final_docx, refs, log, decisions, records,
                            preexisting=(rep.get("existing_citation_fields") or {}).get("endnote", 0))
    print(json.dumps(rt, indent=2))
    print("\nRound trip:", "OK, every converted citation is a live EndNote citation to the expected reference."
          if rt["ok"] else "PROBLEMS FOUND (see above).")
    return 0 if rt["ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="*", help="files or folders: .docx .pdf .txt .md .rtf .csv .tsv .xlsx "
                                             "(.doc/.odt need LibreOffice)")
    ap.add_argument("--email", default=os.getenv("NCBI_EMAIL"), help="contact email sent to NCBI")
    ap.add_argument("--api-key", default=os.getenv("NCBI_API_KEY"), help="NCBI API key (optional)")
    ap.add_argument("--outdir", help="output folder (default: next to each input file)")
    ap.add_argument("--heading", help='reference section title if not "References"')
    ap.add_argument("--config", help="JSON settings file (thresholds, cache, ...)")
    ap.add_argument("--offline", action="store_true", help="answer only from the local PubMed cache")
    ap.add_argument("--grobid", metavar="URL", help="read PDF references with a local GROBID server")
    ap.add_argument("--grobid-allow-remote", action="store_true",
                    help="allow a GROBID server that is not on this computer (the PDF is sent to it)")
    ap.add_argument("--no-cache", action="store_true", help="do not read or write the local PubMed cache")
    ap.add_argument("--accept-review", action="store_true", help="also accept REVIEW suggestions")
    ap.add_argument("--pmid", action="append", default=[], metavar="REF=PMID",
                    help="force a PMID for a reference (empty value = treat as unmatched); repeatable")
    ap.add_argument("--record-start", type=int, default=1, help="first EndNote record number (default 1)")
    ap.add_argument("--no-record-numbers", action="store_true", help="write {Author, Year} only")
    ap.add_argument("--no-superscript", action="store_true")
    ap.add_argument("--no-brackets", action="store_true")
    ap.add_argument("--paren", action="store_true", help="also convert (1,2) style citations")
    ap.add_argument("--glued", choices=["auto", "on", "off"], default="auto",
                    help="numbers attached to words, e.g. classification1,2 (default: auto detect)")
    ap.add_argument("--keep-reference-list", action="store_true",
                    help="do not remove the old reference list from the Word copy")
    ap.add_argument("--audit-only", action="store_true", help="audit citations but write no Word copy")
    ap.add_argument("--roundtrip", nargs=2, metavar=("REPORT_JSON", "FINAL_DOCX"),
                    help="check a document after EndNote updated it, against an earlier report")
    ap.add_argument("--sources", metavar="LIST",
                    help="secondary sources after PubMed, comma separated from: crossref, openalex, doi, "
                         "nlmcatalog, doaj, unpaywall, retractionwatch (default: all; 'none' for PubMed only)")
    ap.add_argument("--no-extra", action="store_true", help="PubMed only (same as --sources none)")
    ap.add_argument("--rw-csv", metavar="CSV", help="your own Retraction Watch CSV, used as is (no update)")
    ap.add_argument("--rw-dir", metavar="DIR", help="folder for the Retraction Watch copy that RefCheck updates "
                                                    "each run (default: the RefCheck cache folder)")
    ap.add_argument("--scoring", choices=["points", "evidence"],
                    help="candidate scoring: points (hand set, default) or evidence (fitted weights, needs --weights)")
    ap.add_argument("--weights", metavar="JSON", help="weights file fitted on labelled references (for --scoring evidence)")
    ap.add_argument("--debug", action="store_true", help="print every PubMed query")
    a = ap.parse_args(argv)

    if a.roundtrip:
        return roundtrip(*a.roundtrip)
    if not a.input:
        ap.error("give at least one input file or folder")
    if not a.email:
        sys.exit("error: give --email (or set NCBI_EMAIL); NCBI requires a contact address.")
    settings = load_settings(a.config)
    if a.weights:
        settings.weights_path = a.weights
    if a.scoring:
        settings.scoring = a.scoring
    if settings.scoring == "evidence":
        from refcheck.evidence import model_for
        try:
            m = model_for(settings)
        except (ValueError, OSError) as e:
            sys.exit(f"error: {e}")
        print(f"Scoring: evidence weights from {Path(settings.weights_path).name} "
              f"(fitted {m.data.get('fitted_on', {}).get('date', '?')}; automatic match at probability "
              f">= {m.auto_probability})")
    if a.no_cache:
        settings.cache_path = ""
    client = PubMedClient(a.email, a.api_key, log=(lambda m: print("   ", m)) if a.debug else None,
                          settings=settings, offline=a.offline or None)
    if a.no_extra:
        settings.sources = []
    elif a.sources:
        settings.sources = [] if a.sources.lower() in ("none", "off") else \
            [x.strip().lower() for x in a.sources.split(",") if x.strip()]
        unknown = [x for x in settings.sources if x not in ALL_SOURCES]
        if unknown:
            sys.exit("error: unknown source(s): " + ", ".join(unknown))
    if a.rw_csv:
        settings.retraction_watch_csv = a.rw_csv
    if a.rw_dir:
        settings.retraction_watch_dir = a.rw_dir
    evidence = evidence_from_settings(settings, client, a.email)
    if evidence is not None:
        print("Secondary sources:", ", ".join(sorted(evidence.enabled)) +
              (" + local Retraction Watch file" if evidence.rw else ""))
        if evidence.rw_status is not None:
            print("  " + evidence.rw_status.line())
    files = expand_inputs(a.input)
    if len(files) > 1 and a.pmid:
        sys.exit("error: --pmid applies to one input file at a time")
    failures = 0
    for i, src in enumerate(files, 1):
        if len(files) > 1:
            print(f"\n=== [{i}/{len(files)}] {src}")
        try:
            run_one(src, a, settings, client, evidence)
        except Exception as e:          # keep a batch going
            failures += 1
            print(f"error: {src.name}: {e}")
            if len(files) == 1:
                raise
    if len(files) > 1:
        print(f"\nBatch finished: {len(files) - failures} of {len(files)} files processed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
