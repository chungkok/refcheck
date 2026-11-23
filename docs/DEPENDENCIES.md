# Dependency and licence inventory

RefCheck itself is Apache 2.0 (`LICENSE`). Versions are those in `requirements-lock.txt`;
licences as declared in each package's metadata on 21 November 2026.

| Package | Version | Licence | Used for |
|---|---|---|---|
| streamlit | 1.67.0 | Apache 2.0 | user interface |
| pandas | 3.0.5 | BSD 3 clause | tables in the interface |
| numpy | 2.5.3 | BSD 3 clause and others (0BSD, MIT, Zlib, CC0) | pandas dependency |
| pyarrow | 26.0.1 | Apache 2.0 | Streamlit dependency |
| requests | 2.34.2 | Apache 2.0 | NCBI and GROBID requests |
| python-docx | 1.2.0 | MIT | reading and writing Word files |
| lxml | 6.1.3 | BSD 3 clause | python-docx dependency, XML writing |
| pypdf | 7.1.1 | BSD 3 clause | PDF text extraction |
| openpyxl | 3.1.5 | MIT | Excel input and reports |

Optional or development only:

| Package or program | Licence | Used for |
|---|---|---|
| defusedxml | PSF | hardened XML parsing when installed |
| pytest | MIT | tests |
| bandit | Apache 2.0 | static security analysis in CI |
| pip-audit | Apache 2.0 | dependency vulnerability check in CI |
| LibreOffice | MPL 2.0 | `.doc` and `.odt` conversion (separate program) |
| GROBID | Apache 2.0 | optional PDF extraction (separate program) |

External service: NCBI E-utilities (public service of the US National Library of Medicine). NCBI's
usage guidelines apply: identify the tool and a contact email, and keep to 3 requests per second
without an API key or 10 with one.

## Security status

* `bandit` on `refcheck/`, `app.py`, `refcheck_cli.py`: no findings (justified
  exceptions are marked inline with `# nosec` and a reason).
* `pip-audit -r requirements-lock.txt`: no known vulnerabilities on 21 November 2026.
  pypdf 6.17.0, the version previously in use, had 25 known CVEs and was replaced by 7.1.1.
  Extraction from the test PDFs was identical before and after.
