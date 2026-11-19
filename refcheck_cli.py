#!/usr/bin/env python3
"""Command line version of RefCheck.

Examples
    python refcheck_cli.py manuscript.docx --email you@example.org
    python refcheck_cli.py refs.pdf --email you@example.org --outdir results
    python refcheck_cli.py manuscript.docx --email you@x.org --accept-review --paren
    python refcheck_cli.py manuscript.docx --email you@x.org --pmid 6=12345678 --pmid 9=

Set NCBI_EMAIL / NCBI_API_KEY in the environment to skip the flags.
"""

import argparse
import os
import sys
from pathlib import Path

from refcheck.extract import extract_references
from refcheck.pipeline import apply_overrides, build_outputs, default_decisions, run_matching
from refcheck.pubmed import PubMedClient


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help=".docx .pdf .txt .md .rtf .csv .tsv .xlsx (.doc/.odt need LibreOffice)")
    ap.add_argument("--email", default=os.getenv("NCBI_EMAIL"), help="contact email sent to NCBI")
    ap.add_argument("--api-key", default=os.getenv("NCBI_API_KEY"), help="NCBI API key (optional)")
    ap.add_argument("--outdir", help="output folder (default: next to the input file)")
    ap.add_argument("--heading", help='reference section title if not "References"')
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
    ap.add_argument("--debug", action="store_true", help="print every PubMed query")
    a = ap.parse_args()

    if not a.email:
        sys.exit("error: give --email (or set NCBI_EMAIL); NCBI requires a contact address.")
    src = Path(a.input)
    ex = extract_references(src, a.heading)
    refs = ex["refs"]
    for w in ex["warnings"]:
        print("warning:", w)
    print(f"{len(refs)} references found in {src.name}")

    client = PubMedClient(a.email, a.api_key, log=(lambda m: print("   ", m)) if a.debug else None)

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
        ex, refs, results, decisions, client, Path(a.outdir or src.parent), src.stem,
        record_start=a.record_start, remove_reference_list=not a.keep_reference_list,
        annotate_opts=dict(brackets=not a.no_brackets, superscript=not a.no_superscript,
                           parentheses=a.paren, glued=glued,
                           use_record_numbers=not a.no_record_numbers))
    for w in out["warnings"]:
        print("warning:", w)
    counts = {}
    for _, st in decisions.values():
        counts[st] = counts.get(st, 0) + 1
    print("\nSummary:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for k, p in out["paths"].items():
        print(f"  {k:5s} {p}")


if __name__ == "__main__":
    main()
