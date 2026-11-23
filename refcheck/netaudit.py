"""Local record of every request RefCheck makes to NCBI, and a disk cache.

The audit lists exactly which fields left the computer, so the user can see that
only bibliographic search terms (never manuscript text) are transmitted. It is
kept in memory for the session and can be exported with the report.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# Parameters that never describe the manuscript. The API key is redacted.
_CONTROL = {"db", "retmode", "retmax", "sort", "tool", "rows", "per_page", "select", "pageSize"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Request:
    time: str
    endpoint: str
    transmitted: Dict[str, str]      # parameter -> value, API key redacted
    from_cache: bool
    status: str                      # ok | cached | error kind
    bytes_received: int = 0
    duration_s: float = 0.0
    host: str = "eutils.ncbi.nlm.nih.gov"


@dataclass
class NetworkAudit:
    requests: List[Request] = field(default_factory=list)

    def add(self, endpoint: str, params: Dict[str, Any], from_cache: bool, status: str,
            nbytes: int = 0, duration: float = 0.0, host: str = "eutils.ncbi.nlm.nih.gov") -> None:
        sent = {}
        for k, v in params.items():
            if k in ("api_key", "mailto_key"):
                sent[k] = "(redacted)"
            elif k in ("email", "mailto"):
                sent[k] = "(contact email)"
            else:
                sent[k] = str(v)
        self.requests.append(Request(_now(), endpoint, sent, from_cache, status,
                                     nbytes, round(duration, 3), host))

    # ------------------------------------------------------------------ views --
    def summary(self) -> Dict[str, Any]:
        net = [r for r in self.requests if not r.from_cache]
        by_ep: Dict[str, int] = {}
        for r in net:
            by_ep[r.endpoint] = by_ep.get(r.endpoint, 0) + 1
        fields = sorted({k for r in net for k in r.transmitted if k not in _CONTROL})
        return dict(
            network_requests=len(net),
            answered_from_cache=len(self.requests) - len(net),
            errors=sum(1 for r in net if r.status not in ("ok",)),
            by_endpoint=by_ep,
            bytes_received=sum(r.bytes_received for r in net),
            destination=", ".join(sorted({r.host for r in net})) or "none",
            by_destination={h: sum(1 for r in net if r.host == h) for h in sorted({r.host for r in net})},
            parameters_sent=fields,
        )

    def transmitted_values(self) -> List[str]:
        """Every value sent over the network (for tests and the privacy panel)."""
        return [v for r in self.requests if not r.from_cache
                for k, v in r.transmitted.items() if k not in _CONTROL | {"email", "api_key", "mailto"}]

    def to_list(self) -> List[Dict[str, Any]]:
        return [asdict(r) for r in self.requests]


class DiskCache:
    """SQLite cache of raw E-utilities responses keyed by endpoint and query.

    The key never includes the email or API key, so a shared cache does not leak
    them. Entries older than ``ttl_days`` are ignored (PubMed records change, for
    example when a retraction is added)."""

    def __init__(self, path: str, ttl_days: float = 30):
        self.path = Path(path)
        self.ttl = ttl_days * 86400
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, "
                      "endpoint TEXT, stored REAL, body TEXT)")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Connection that commits on success and is always closed."""
        c = sqlite3.connect(str(self.path), timeout=10)
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def key(endpoint: str, params: Dict[str, Any]) -> str:
        p = {k: str(v) for k, v in params.items() if k not in ("email", "api_key", "tool", "mailto")}
        raw = endpoint + "\n" + json.dumps(p, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, endpoint: str, params: Dict[str, Any], ignore_ttl: bool = False) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT stored, body FROM responses WHERE key = ?",
                            (self.key(endpoint, params),)).fetchone()
        if not row:
            return None
        if not ignore_ttl and time.time() - row[0] > self.ttl:
            return None
        return row[1]

    def put(self, endpoint: str, params: Dict[str, Any], body: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?)",
                      (self.key(endpoint, params), endpoint, time.time(), body))

    def clear(self) -> int:
        with self._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
            c.execute("DELETE FROM responses")
        return n

    def stats(self) -> Dict[str, Any]:
        with self._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        return dict(path=str(self.path), entries=n,
                    size_kb=round(self.path.stat().st_size / 1024, 1) if self.path.exists() else 0)
