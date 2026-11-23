"""Runtime settings.

Values come from, in increasing priority:
    1. the defaults below
    2. a JSON file named by REFCHECK_CONFIG (for example thresholds fitted on labelled data)
    3. individual environment variables (REFCHECK_AUTO_SCORE and so on)

The matching thresholds are a *score*, not a probability. The defaults are the
hand chosen values of RefCheck 1.x and are marked ``threshold_source =
"provisional"`` until replaced by thresholds fitted on a labelled benchmark.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

_ENV = {
    "auto_score": ("REFCHECK_AUTO_SCORE", float),
    "review_score": ("REFCHECK_REVIEW_SCORE", float),
    "min_gap": ("REFCHECK_MIN_GAP", float),
    "cache_path": ("REFCHECK_CACHE", str),
    "cache_ttl_days": ("REFCHECK_CACHE_TTL_DAYS", float),
    "offline": ("REFCHECK_OFFLINE", lambda v: v.strip().lower() in ("1", "true", "yes", "on")),
    "deployment": ("REFCHECK_DEPLOYMENT", str),
    "max_upload_mb": ("REFCHECK_MAX_UPLOAD_MB", int),
    "ncbi_email": ("NCBI_EMAIL", str),
    "ncbi_api_key": ("NCBI_API_KEY", str),
    "sources": ("REFCHECK_SOURCES", lambda v: [] if v.strip().lower() in ("none", "off") else
                [x.strip().lower() for x in v.split(",") if x.strip()]),
    "retraction_watch_csv": ("REFCHECK_RW_CSV", str),
    "retraction_watch_dir": ("REFCHECK_RW_DIR", str),
    "scoring": ("REFCHECK_SCORING", str),
    "weights_path": ("REFCHECK_WEIGHTS", str),
    "disabled": ("REFCHECK_DISABLE", lambda v: [x.strip() for x in v.split(",") if x.strip()]),
}


def default_cache_path() -> str:
    base = os.getenv("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(base) / "refcheck" / "pubmed_cache.sqlite")


@dataclass
class Settings:
    auto_score: float = 86
    review_score: float = 70
    min_gap: float = 5
    threshold_source: str = "provisional (RefCheck 1.x hand chosen values, not calibrated)"
    cache_path: str = field(default_factory=default_cache_path)   # "" disables the disk cache
    cache_ttl_days: float = 30
    offline: bool = False            # True: answer only from the local cache
    deployment: str = "auto"         # auto | local | hosted
    max_upload_mb: int = 50
    ncbi_email: str = ""
    ncbi_api_key: str = ""
    # Secondary sources checked after PubMed (sources.ALL_SOURCES). Empty: PubMed only.
    sources: list = field(default_factory=lambda: ["crossref", "openalex", "doi", "nlmcatalog", "doaj",
                                                   "unpaywall", "retractionwatch"])
    # Your own copy of the Retraction Watch CSV. When set it is used as is and never updated.
    retraction_watch_csv: str = ""
    # Where the "retractionwatch" source keeps the copy it updates each run ('' = cache folder)
    retraction_watch_dir: str = ""
    # Candidate scoring: "points" (hand set, RefCheck 1.x) or "evidence" (likelihood ratio
    # weights, prior and calibration fitted from labelled data, read from a weights file).
    # Evidence scoring thresholds are probabilities and come from the weights file.
    scoring: str = "points"
    weights_path: str = ""
    # Ablation switches for evaluation (leave empty in normal use). Names:
    #   search steps: pmid-in-text, doi, citmatch, journal-vol-page, author-year-page,
    #                 title-phrase, title+author, title+year, title-words, title-words-loose, text-words
    #   rules: abstention (author/year/title vetoes), notice-rival (ignore erratum runner up),
    #          online-first (accept the electronic year), early-stop
    disabled: list = field(default_factory=list)

    def public_dict(self) -> Dict[str, Any]:
        """Settings safe to write into a report (no API key)."""
        d = asdict(self)
        d["ncbi_api_key"] = "set" if self.ncbi_api_key else ""
        return d


def load_settings(path: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Settings:
    env = dict(os.environ if env is None else env)
    s = Settings()
    cfg = path or env.get("REFCHECK_CONFIG")
    if cfg:
        data = json.loads(Path(cfg).read_text(encoding="utf-8"))
        for k, v in data.items():
            if hasattr(s, k):
                setattr(s, k, v)
        if "auto_score" in data and "threshold_source" not in data:
            s.threshold_source = f"config file {Path(cfg).name}"
    for attr, (name, conv) in _ENV.items():
        if env.get(name, "") != "":
            setattr(s, attr, conv(env[name]))
            if attr in ("auto_score", "review_score", "min_gap"):
                s.threshold_source = "environment variables"
    if s.review_score > s.auto_score:
        raise ValueError("review_score must not exceed auto_score")
    return s
