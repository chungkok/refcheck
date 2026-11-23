# RefCheck

Bibliographic integrity checking and citation reconciliation for biomedical manuscripts.

RefCheck extracts the reference list from a manuscript, identifies each reference in PubMed,
reports whether its details are correct (wrong DOI or PMID, wrong year or pages, duplicates,
retracted or corrected papers), audits the numbered citations in the text against the list, and
prepares an EndNote library plus a Word copy whose citations EndNote can take over. It runs on your
own computer: only bibliographic search terms and identifiers are sent, to PubMed and to the
optional secondary sources you choose (Crossref, OpenAlex, doi.org, NLM Catalog, DOAJ, Unpaywall).

<p align="center">
  <a href="https://refcheck.streamlit.app/">
    <img src="https://img.shields.io/badge/Live_demo-refcheck.streamlit.app-brightgreen?style=for-the-badge" alt="RefCheck live demo">
  </a>
</p>

> **The live demo runs on a remote server.** Files you upload there leave your computer. Use it
> with published papers or test files. For unpublished manuscripts, install RefCheck locally.
> See [docs/PRIVACY.md](docs/PRIVACY.md).

## Start

```
run_app.bat            (Windows: double click)
./run_app.sh           (Linux, macOS, WSL)
```

The first run creates a private Python environment with the tested versions and opens
http://localhost:8501. Enter your email in the sidebar (NCBI asks every tool for a contact
address). An NCBI API key is optional and makes searching about three times faster.
More options, Docker and institutional hosting: [docs/INSTALLATION.md](docs/INSTALLATION.md).

Command line, same engine:

```
python refcheck_cli.py manuscript.docx --email you@example.org
python refcheck_cli.py papers/ --email you@example.org --outdir results          # batch
python refcheck_cli.py --roundtrip results/manuscript_integrity_report.json final.docx
```

## What it does

| Step | Detail |
|---|---|
| Extract | Word, PDF (text layer, optional local GROBID), text, Markdown, RTF, CSV, TSV, Excel, BibTeX, .bbl and LaTeX `thebibliography`; DOC and ODT via LibreOffice. Vancouver, AMA, APA, Frontiers and EndNote output styles. Unsure fields are flagged for correction. |
| Identify | PubMed by written PMID, DOI, NCBI citation matcher, journal, volume and page, author and year, then title. Refuses to accept automatically when evidence conflicts, and says why. |
| Verify | 14 integrity statuses, from VERIFIED to RETRACTED, each with field level findings and their source. Optional secondary evidence: retractions, corrections and expressions of concern from Crossref (publisher and Retraction Watch), OpenAlex and the Retraction Watch dataset (a local copy, updated from GitLab only when a new version is published); written DOIs checked at Crossref and doi.org; references missing from PubMed looked up in Crossref and OpenAlex; journals checked by ISSN check digit, NLM Catalog (MEDLINE indexing) and DOAJ; open access copies from Unpaywall. A reference not found in any source is never called fabricated. |
| Audit | Every numbered citation in body text, tables, figure legends, footnotes and endnotes, mapped to the list: citations without a reference, uncited and duplicate references, citation order, mixed styles. Gene names, units and exponents are not mistaken for citations. |
| EndNote | BibTeX export and a suggested Vancouver text for every matched reference. RIS and EndNote XML with your reference numbers as Labels and PMIDs as accession numbers; a Word copy with EndNote temporary citations; a check of the document after EndNote has updated it. |
| Report | HTML, JSON, Excel and CSV reports with software version, thresholds, your decisions kept apart from automatic results, and every network request made. |

User guide: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Status

RefCheck 2.0 is in development on the `refcheck-2.0` branch. The functions above are implemented
and covered by offline tests. **Their accuracy has not yet been measured on an independent
benchmark**, and the matching thresholds are provisional values, not calibrated probabilities.
The benchmarks, validation scripts and stress test manuscripts are kept in a separate validation
package that goes with the methods paper; this repository holds the program. Results so far and
the feature by feature status (implemented, partial, planned) are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#status-of-each-feature).

## Documentation

| Document | Content |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | modules, diagrams, design decisions, feature status |
| [ALGORITHM_SPECIFICATION](docs/ALGORITHM_SPECIFICATION.md) | every rule, weight and threshold |
| [PRIVACY](docs/PRIVACY.md), [DATA_FLOW](docs/DATA_FLOW.md) | what leaves the computer, what is stored and for how long |
| [INSTALLATION](docs/INSTALLATION.md), [USER_GUIDE](docs/USER_GUIDE.md) | running and using RefCheck |
| [DEPENDENCIES](docs/DEPENDENCIES.md), [CHANGELOG](CHANGELOG.md) | licences, security status, changes |

## Tests

```
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests run offline: PubMed is replaced by saved real records.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
