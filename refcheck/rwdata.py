"""Keep a local copy of the Retraction Watch dataset up to date.

Crossref publishes the dataset as ``retraction_watch.csv`` on the ``main`` branch of
https://gitlab.com/crossref/retraction-watch-data and updates it once per working day at
about 23:00 UTC (Crossref community forum, 11 October 2024).

Each run asks GitLab for the file's metadata only (a HEAD request to the repository files
API, which answers with ``X-Gitlab-Content-Sha256`` and ``X-Gitlab-Blob-Id``). When the
checksum equals the local copy's, nothing is downloaded. Otherwise the file is downloaded
to a temporary name, its SHA-256 is checked against the advertised one, its columns and row
count are checked, and only then does it replace the local copy. If GitLab cannot be
reached, or the download fails any check, the previous copy stays in use and the result
says how old it is. Nothing from the manuscript is sent: the requests name only the file.

When GitLab gives no checksum, a conditional download (``If-None-Match`` with the stored
ETag) is used, and a downloaded file identical to the local copy is discarded.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests

PROJECT = "crossref%2Fretraction-watch-data"
FILE = "retraction_watch.csv"
BRANCH = "main"
API = f"https://gitlab.com/api/v4/projects/{PROJECT}/repository/files/{FILE}"
META_URL = f"{API}?ref={BRANCH}"
RAW_URL = f"{API}/raw?ref={BRANCH}"
HOST = "gitlab.com"
REQUIRED_COLUMNS = {"Record ID", "OriginalPaperDOI", "OriginalPaperPubMedID", "RetractionNature",
                    "RetractionDate"}
MIN_ROW_FRACTION = 0.9      # a new file with fewer than 90% of the previous rows is refused
CHUNK = 1 << 20


def default_dir() -> str:
    base = os.getenv("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(base) / "refcheck" / "retraction_watch")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def check_csv(path: Path) -> int:
    """Row count of a Retraction Watch CSV; ValueError when the columns are wrong."""
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        missing = REQUIRED_COLUMNS - {h.strip() for h in header}
        if missing:
            raise ValueError("missing columns: " + ", ".join(sorted(missing)))
        return sum(1 for _ in reader)


@dataclass
class UpdateResult:
    status: str             # downloaded | unchanged | stale | offline | unavailable | disabled
    path: str = ""          # the CSV to use ('' when there is none)
    message: str = ""
    sha256: str = ""
    rows: int = 0
    last_commit: str = ""
    downloaded_at: str = ""
    checked_at: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.path)

    def public(self) -> Dict[str, Any]:
        """For the report: which dataset version was used."""
        d = asdict(self)
        d["path"] = Path(self.path).name if self.path else ""     # no local folder names in reports
        d["source"] = f"https://gitlab.com/crossref/retraction-watch-data ({FILE}, {BRANCH})"
        return d

    def line(self) -> str:
        """One sentence for people: which version, and whether it was updated."""
        if not self.usable:
            return f"Retraction Watch not checked: {self.message}."
        ver = f"{self.rows} records" if self.rows else "records"
        when = f", downloaded {self.downloaded_at}" if self.downloaded_at else ""
        sha = f", SHA-256 {self.sha256[:12]}" if self.sha256 else ""
        return f"Retraction Watch dataset ({ver}{when}{sha}): {self.message}."


class RetractionWatchUpdater:
    """Tests pass a fake ``session`` with ``head`` and ``get``."""

    def __init__(self, directory: str = "", session=None, audit=None, timeout: float = 30):
        self.dir = Path(directory or default_dir())
        self.csv = self.dir / FILE
        self.state_path = self.dir / "retraction_watch.state.json"
        self.session = session or requests.Session()
        self.audit = audit
        self.timeout = timeout

    # ------------------------------------------------------------- state --
    def state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, st: Dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _result(self, status: str, message: str, st: Dict[str, Any]) -> UpdateResult:
        return UpdateResult(status, str(self.csv) if self.csv.exists() else "", message,
                            st.get("sha256", ""), int(st.get("rows", 0) or 0), st.get("last_commit", ""),
                            st.get("downloaded_at", ""), st.get("checked_at", ""))

    def _log(self, endpoint: str, status: str, nbytes: int = 0, t0: float = 0.0) -> None:
        if self.audit is not None:
            self.audit.add(endpoint, {"file": FILE, "ref": BRANCH}, False, status, nbytes,
                           time.time() - t0 if t0 else 0.0, host=HOST)

    # ------------------------------------------------------------ public --
    def local(self, why: str = "offline") -> UpdateResult:
        """Use the local copy without any network request."""
        st = self.state()
        if not self.csv.exists():
            return self._result("unavailable", "no local Retraction Watch copy yet", st)
        return self._result(why, f"using the local copy downloaded {st.get('downloaded_at') or 'at an unknown time'}",
                            st)

    def update(self, force: bool = False) -> UpdateResult:
        self.dir.mkdir(parents=True, exist_ok=True)
        st = self.state()
        have = self.csv.exists()
        local_sha = file_sha256(self.csv) if have else ""
        if have and st.get("sha256") and st["sha256"] != local_sha:
            st = {}                          # the file was changed by hand: trust nothing stored
        meta = self._meta()
        if meta is None and have:
            return self._fallback_get(st, local_sha, force)
        # (no metadata and nothing local: try the download anyway)
        remote_sha = (meta or {}).get("sha256", "")
        remote_blob = (meta or {}).get("blob_id", "")
        same = have and not force and (
            (remote_sha and remote_sha == local_sha) or
            (not remote_sha and remote_blob and remote_blob == st.get("blob_id")))
        if same:
            st.update(checked_at=_now(), sha256=local_sha, blob_id=remote_blob or st.get("blob_id", ""),
                      last_commit=(meta or {}).get("last_commit") or st.get("last_commit", ""))
            st.setdefault("rows", self._rows_or_zero())
            self._save_state(st)
            return self._result("unchanged", "local copy is the current version; not downloaded", st)
        return self._download(st, local_sha, meta or {})

    # ----------------------------------------------------------- helpers --
    def _rows_or_zero(self) -> int:
        try:
            return check_csv(self.csv)
        except (OSError, ValueError):
            return 0

    def _meta(self) -> Optional[Dict[str, str]]:
        t0 = time.time()
        try:
            r = self.session.head(META_URL, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException:
            self._log("retraction-watch metadata", "connection", 0, t0)
            return None
        h = {k.lower(): v for k, v in (r.headers or {}).items()}
        if r.status_code != 200:
            self._log("retraction-watch metadata", f"http_{r.status_code}", 0, t0)
            return None
        self._log("retraction-watch metadata", "ok", 0, t0)
        return dict(sha256=(h.get("x-gitlab-content-sha256") or "").lower(), blob_id=h.get("x-gitlab-blob-id", ""),
                    last_commit=h.get("x-gitlab-last-commit-id", ""), size=h.get("x-gitlab-size", ""))

    def _fallback_get(self, st, local_sha, force) -> UpdateResult:
        """Metadata unavailable: conditional download when an ETag is known, else keep the copy."""
        if force or st.get("etag"):
            return self._download(st, local_sha, {}, conditional=not force)
        return self._keep(st, "GitLab metadata not available")

    def _download(self, st, local_sha, meta, conditional: bool = False) -> UpdateResult:
        headers = {"If-None-Match": st["etag"]} if conditional and st.get("etag") else {}
        tmp = self.csv.with_suffix(".csv.part")
        t0 = time.time()
        h = hashlib.sha256()
        n = 0
        try:
            r = self.session.get(RAW_URL, headers=headers, stream=True, timeout=self.timeout)
            if r.status_code == 304:
                self._log("retraction-watch download", "not_modified", 0, t0)
                st["checked_at"] = _now()
                self._save_state(st)
                return self._result("unchanged", "local copy is the current version (ETag); not downloaded", st)
            if r.status_code != 200:
                self._log("retraction-watch download", f"http_{r.status_code}", 0, t0)
                return self._keep(st, f"download failed (HTTP {r.status_code})")
            with open(tmp, "wb") as fh:
                for block in r.iter_content(CHUNK):
                    if block:
                        fh.write(block)
                        h.update(block)
                        n += len(block)
        except (requests.RequestException, OSError) as e:
            self._log("retraction-watch download", "connection", n, t0)
            tmp.unlink(missing_ok=True)
            return self._keep(st, f"download failed ({type(e).__name__})")
        self._log("retraction-watch download", "ok", n, t0)
        new_sha = h.hexdigest()
        if meta.get("sha256") and new_sha != meta["sha256"]:
            tmp.unlink(missing_ok=True)
            return self._keep(st, "downloaded file does not match GitLab's SHA-256; not used")
        if new_sha == local_sha:
            tmp.unlink(missing_ok=True)
            st.update(checked_at=_now(), sha256=new_sha, etag=_etag(r) or st.get("etag", ""))
            self._save_state(st)
            return self._result("unchanged", "downloaded file is identical to the local copy; kept the local copy", st)
        try:
            rows = check_csv(tmp)
        except (OSError, ValueError) as e:
            tmp.unlink(missing_ok=True)
            return self._keep(st, f"downloaded file is not a Retraction Watch CSV ({e})")
        prev = int(st.get("rows", 0) or 0)
        if prev and rows < MIN_ROW_FRACTION * prev:
            tmp.unlink(missing_ok=True)
            return self._keep(st, f"downloaded file has {rows} rows, previous copy {prev}; not used")
        os.replace(tmp, self.csv)
        now = _now()
        st = dict(sha256=new_sha, blob_id=meta.get("blob_id", ""), last_commit=meta.get("last_commit", ""),
                  etag=_etag(r), rows=rows, downloaded_at=now, checked_at=now, bytes=n)
        self._save_state(st)
        return self._result("downloaded", f"downloaded the current version ({rows} records)", st)

    def _keep(self, st, why: str) -> UpdateResult:
        if self.csv.exists():
            return self._result("stale", why + f"; using the local copy downloaded "
                                f"{st.get('downloaded_at') or 'at an unknown time'}", st)
        return self._result("unavailable", why + "; no local copy", st)


def _etag(r) -> str:
    return {k.lower(): v for k, v in (getattr(r, "headers", None) or {}).items()}.get("etag", "")


def ensure_current(directory: str = "", offline: bool = False, session=None, audit=None,
                   force: bool = False) -> UpdateResult:
    """The one call the pipeline makes. Offline mode never touches the network."""
    up = RetractionWatchUpdater(directory, session=session, audit=audit)
    if offline:
        return up.local("offline")
    return up.update(force=force)


__all__ = ["ensure_current", "RetractionWatchUpdater", "UpdateResult", "check_csv", "default_dir",
           "file_sha256"]
