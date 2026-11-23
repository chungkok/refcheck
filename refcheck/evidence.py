"""Evidence based candidate scoring (likelihood ratios, Fellegi and Sunter 1969).

Every field comparison between a reference and a PubMed candidate falls into one level
(compare_fields). For each level two probabilities are estimated from labelled data:

    m = P(level | the candidate is the cited work)
    u = P(level | the candidate is another work returned by the same searches)

and the level's weight is log2(m / u): how many times more likely this comparison is for
the right paper than for a wrong one. A candidate's log odds is the prior log odds plus
the sum of its weights; a logistic calibration fitted on the same data turns it into a
probability. A missing field (absent from the reference or the record) is not evidence
and weighs 0. The weights, their intervals, the prior and the calibration come from a
weights file fitted on labelled references; nothing here is chosen by hand.

compare_fields uses exactly the comparisons of pubmed.score_candidate, so the hand set
points of RefCheck 1.x can be rebuilt from the recorded levels (points_from_levels) and
the two scoring methods can be compared on the same candidates.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Optional

from .textutil import clean_text, compact, first_page, token_overlap

FIELDS = {
    "title": ["t98", "t94", "t88", "t80", "w75", "w60", "low"],
    "first_author": ["exact", "prefix5", "differs", "missing"],
    "year": ["same", "online_first", "off1", "off_more", "missing"],
    "journal": ["exact", "partial", "differs", "missing"],
    "volume": ["agree", "differs", "missing"],
    "first_page": ["agree", "differs", "missing"],
    "doi": ["agree", "differs", "missing"],
    "pmid": ["agree", "differs", "missing"],
}
LEVEL_TEXT = {
    "t98": "title similarity at least 0.98", "t94": "title similarity 0.94 to 0.98",
    "t88": "title similarity 0.88 to 0.94", "t80": "title similarity 0.80 to 0.88",
    "w75": "title similarity below 0.80, word overlap at least 0.75",
    "w60": "title similarity below 0.80, word overlap 0.60 to 0.75", "low": "title differs",
    "exact": "agrees", "prefix5": "first five letters agree", "differs": "differs", "missing": "missing",
    "same": "same year", "online_first": "same as the online first year", "off1": "one year apart",
    "off_more": "more than one year apart", "partial": "one title contains the other", "agree": "agrees",
}


def compare_fields(ref: Dict[str, Any], cand: Dict[str, Any], online_first: bool = True) -> Dict[str, str]:
    """One level per field, using the comparisons of pubmed.score_candidate."""
    from .pubmed import _norm_doi, _years_match, first_author_any_style, title_similarity_any_style
    lv: Dict[str, str] = {}
    ts = title_similarity_any_style(ref, cand)
    to = token_overlap(ref.get("title", ""), cand.get("title", ""))
    lv["title"] = ("t98" if ts >= 0.98 else "t94" if ts >= 0.94 else "t88" if ts >= 0.88 else
                   "t80" if ts >= 0.80 else "w75" if to >= 0.75 else "w60" if to >= 0.60 else "low")
    lv["first_author"] = first_author_any_style(ref, cand)
    dy = _years_match(ref.get("year", ""), cand, online_first)
    if dy is None:
        lv["year"] = "missing"
    elif dy == 0:
        lv["year"] = "same" if ref.get("year") == cand.get("year") else "online_first"
    else:
        lv["year"] = "off1" if dy == 1 else "off_more"
    rj = compact(ref.get("journal", ""))
    cjs = [compact(cand.get("journal", "")), compact(cand.get("iso_journal", ""))]
    if not rj or not any(cjs):
        lv["journal"] = "missing"
    elif rj in cjs:
        lv["journal"] = "exact"
    elif any(c and (rj in c or c in rj) for c in cjs):
        lv["journal"] = "partial"
    else:
        lv["journal"] = "differs"
    rv, cv = clean_text(ref.get("volume", "")), clean_text(cand.get("volume", ""))
    lv["volume"] = "missing" if not rv or not cv else ("agree" if rv == cv else "differs")
    rp, cp = first_page(ref.get("pages", "")), first_page(cand.get("pages", ""))
    lv["first_page"] = "missing" if not rp or not cp else ("agree" if rp == cp else "differs")
    rd, cd = _norm_doi(ref.get("doi") or ""), _norm_doi(cand.get("doi") or "")
    lv["doi"] = "missing" if not rd or not cd else ("agree" if rd == cd else "differs")
    hint = ref.get("pmid_hint") or ""
    lv["pmid"] = "missing" if not hint else ("agree" if hint == cand.get("pmid") else "differs")
    return lv


_POINTS = {"title": {"t98": 60, "t94": 54, "t88": 46, "t80": 34, "w75": 30, "w60": 20},
           "first_author": {"exact": 15, "prefix5": 8},
           "year": {"same": 10, "online_first": 10, "off1": 2, "off_more": -12},
           "journal": {"exact": 8, "partial": 5}, "volume": {"agree": 3}, "first_page": {"agree": 5},
           "doi": {"agree": 25}, "pmid": {"agree": 30}}


def points_from_levels(lv: Dict[str, str]) -> int:
    """The hand set points of RefCheck 1.x (pubmed.score_candidate), rebuilt from levels."""
    if lv["title"] == "low":
        if lv["doi"] != "agree" and lv["pmid"] != "agree":
            return 0
        base = 10
    else:
        base = _POINTS["title"][lv["title"]]
    return base + sum(_POINTS[f].get(lv[f], 0) for f in _POINTS if f != "title")


class WeightModel:
    """Weights, prior and calibration read from a weights file (format refcheck-weights/1)."""

    def __init__(self, data: Dict[str, Any], source: str = ""):
        if data.get("format") != "refcheck-weights/1":
            raise ValueError("not a RefCheck weights file (format refcheck-weights/1)")
        self.data, self.source = data, source
        self.weights = data["weights"]
        self.prior = float(data["prior_log2_odds"])
        cal = data.get("calibration") or {"a": math.log(2), "b": 0.0}
        self.a, self.b = float(cal["a"]), float(cal["b"])
        th = data.get("thresholds") or {}
        self.auto_probability = float(th.get("auto_probability", 0.99))
        self.review_probability = float(th.get("review_probability", 0.80))
        self.min_gap_bits = float(th.get("min_gap_bits", math.log2(10)))

    @classmethod
    def load(cls, path: str) -> "WeightModel":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), source=str(path))

    def log2_odds(self, lv: Dict[str, str]) -> float:
        """Prior log2 odds plus the weight of every field level (missing weighs 0)."""
        total = self.prior
        for f, level in lv.items():
            if level != "missing":
                total += float(self.weights.get(f, {}).get(level, 0.0))
        return total

    def probability(self, log2_odds: float) -> float:
        z = self.a * log2_odds + self.b
        return 1.0 / (1.0 + math.exp(-z)) if z > -700 else 0.0

    def explain(self, lv: Dict[str, str]) -> str:
        parts = [f"{f} {lv[f]} {float(self.weights.get(f, {}).get(lv[f], 0.0)):+.1f}"
                 for f in FIELDS if lv.get(f) and lv[f] != "missing"]
        return "; ".join(parts)


_cache: Dict[str, WeightModel] = {}


def model_for(settings) -> Optional[WeightModel]:
    """The weight model when settings.scoring is 'evidence' (cached per file), else None."""
    if getattr(settings, "scoring", "points") != "evidence":
        return None
    path = getattr(settings, "weights_path", "")
    if not path:
        raise ValueError("scoring 'evidence' needs a weights file (weights_path, REFCHECK_WEIGHTS or --weights), "
                         "fitted on labelled references")
    if path not in _cache:
        _cache[path] = WeightModel.load(path)
    return _cache[path]
