"""RefPrivacy helpers: temporary data lifecycle, safe names, deployment detection.

Design rule: a manuscript is written to disk only inside ``workspace()``, which
removes the folder in a ``finally`` block, so files do not outlive the step that
needs them even when that step fails. Deleting a file removes it from the file
system; RefCheck makes no claim about recovery from disk blocks (SSD wear
levelling, snapshots and cloud storage are outside its control).
"""

from __future__ import annotations

import atexit
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

_PENDING = set()

# What leaves the computer, for the privacy panel and PRIVACY.md.
TRANSMITTED = [
    ("PubMed ESearch", "eutils.ncbi.nlm.nih.gov", "search terms built from one parsed reference: DOI, "
     "journal, year, volume, first page, first author surname, title words or title phrase"),
    ("PubMed ECitMatch", "eutils.ncbi.nlm.nih.gov", "journal | year | volume | first page | first author"),
    ("PubMed EFetch", "eutils.ncbi.nlm.nih.gov", "PMIDs only"),
    ("NLM Catalog ESearch (optional)", "eutils.ncbi.nlm.nih.gov", "journal abbreviation or ISSN"),
    ("All NCBI requests", "eutils.ncbi.nlm.nih.gov", "tool name 'refcheck', your contact email, and your "
     "API key if you entered one (NCBI requires the email)"),
    ("Crossref (optional)", "api.crossref.org", "a DOI; for a reference not found in PubMed, its title, "
     "first author, journal and year; your email as the polite pool contact"),
    ("OpenAlex (optional)", "api.openalex.org", "a DOI; for a reference not found in PubMed, its title; "
     "your email"),
    ("doi.org (optional)", "doi.org", "a DOI that Crossref does not know"),
    ("DOAJ (optional)", "doaj.org", "a journal ISSN"),
    ("Unpaywall (optional)", "api.unpaywall.org", "a DOI and your email (Unpaywall requires it)"),
    ("Retraction Watch update (optional)", "gitlab.com", "nothing from the manuscript: a request for the "
     "checksum of retraction_watch.csv, and the file itself only when GitLab holds a newer version; "
     "the references are then checked against it on this computer"),
]
NEVER_SENT = ("manuscript body text, figure legends, tables, footnotes, abstracts you wrote, "
              "file names, and the reference list as a whole. RefCheck calls no AI service.")


def safe_stem(name: str, default: str = "manuscript") -> str:
    """File name stem that is safe on every platform and cannot escape a folder."""
    stem = Path(str(name).replace("\\", "/")).name
    stem = Path(stem).stem
    stem = re.sub(r"[^\w.\-]+", "_", stem, flags=re.UNICODE).strip("._")
    return (stem or default)[:80]


@contextmanager
def workspace(files: Optional[Dict[str, bytes]] = None, prefix: str = "refcheck_") -> Iterator[Path]:
    """Temporary folder holding `files`, removed afterwards whatever happens."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _PENDING.add(str(d))
    try:
        for name, data in (files or {}).items():
            (d / Path(name).name).write_bytes(data)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
        _PENDING.discard(str(d))


def register_for_cleanup(path) -> None:
    """For folders that must outlive one call (command line runs): removed at exit."""
    _PENDING.add(str(path))


@atexit.register
def _cleanup_pending() -> None:
    for d in list(_PENDING):
        shutil.rmtree(d, ignore_errors=True)
        _PENDING.discard(d)


def pending_temp_folders() -> int:
    return sum(1 for d in _PENDING if Path(d).exists())


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def detect_deployment(setting: str = "auto", host: Optional[str] = None) -> str:
    """'local' or 'hosted'. An explicit setting wins; otherwise the browser's Host header
    decides, and an unknown host is treated as hosted (the cautious answer)."""
    if setting in ("local", "hosted"):
        return setting
    if not host:
        return "hosted"
    if host.startswith("["):                       # [::1]:8501
        h = host[: host.find("]") + 1]
    else:
        h = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return "local" if h.lower() in LOCAL_HOSTS else "hosted"
