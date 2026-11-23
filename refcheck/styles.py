"""Citation style detection, per reference and per manuscript.

Each reference is matched against the layout signatures of common citation styles (where the
year sits, how authors are written, quotation marks, labels such as vol. and pp.). Detection
is reported at two levels:

  family   layouts that can be told apart from one reference reliably (for example the
           Vancouver family, APA, Nature, Harvard, MDPI)
  style    the best guess within the family, from small marks such as a full stop after the
           journal or spaces after the colon. Several journal styles are identical for a single
           reference (NEJM and the current Bioinformatics layout; AMA and Vancouver differ only
           in the page dash), so this level is a guess and is reported as one.

A manuscript's style is the most common family among its detected references. When a second
family accounts for enough references, a mixed style warning lists them: references copied
from different sources, or pasted from a generator, often arrive in mixed styles. Mixed
styles are a reason to look, not evidence of fabrication.

Accuracy is measured on references formatted in 17 CSL styles; nothing here is tuned to a
stated figure.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

YR = r"(?:19|20)\d{2}[a-z]?"
PG = (r"[A-Za-z]{0,4}\d+[A-Za-z]?(?:\s*[" + chr(0x2013) + chr(0x2014) + r"\-]\s*[A-Za-z]{0,4}\d+[A-Za-z]?)?"
      r"(?:\.e\d+)?")
VOL = r"[A-Za-z]?\d+[A-Za-z]?"
# capitals: Latin 1, Latin Extended A (Polish, Czech ...) and the Romanian S and T with comma
UP = "[A-Z" + chr(0xC0) + "-" + chr(0xDE) + chr(0x100) + "-" + chr(0x17F) + chr(0x218) + "-" + chr(0x21B) + "]"
APOS = "'" + chr(0x2019)
INI = UP + r"[a-z]?\.(?:\s?-?" + UP + r"[a-z]?\.)*"             # 'S. P.', 'S.P.', 'C.-I.'
PARTICLE = r"(?:(?:van|von|de|der|den|del|della|da|di|du|dos|das|le|la|ten|ter|al|el|bin|zu)\s)*"
SUR = PARTICLE + UP + r"[\w" + APOS + r"\-]+(?:\s(?:[a-z]{1,3}\s)*" + UP + r"?[\w" + APOS + r"\-]+)*"
# a name written 'Lim S-l' or 'Perez-Benito A' (no comma): surname then a short initials token
BARE = SUR + r"\s" + UP + r"[\w\-]{0,3}"
FIRST = r"(?:" + SUR + r",\s?" + INI + r"|" + BARE + r")"     # first author, surname first
DASHES = chr(0x2013) + chr(0x2014)
QUOTE_OPEN, QUOTE_CLOSE = chr(0x201C) + chr(0x2018) + "\"'", chr(0x201D) + chr(0x2019) + "\"'"


FAMILIES = {
    "vancouver": "Vancouver family (NLM, AMA, BMJ, PLOS, NEJM, Lancet, Bioinformatics)",
    "apa": "APA",
    "nature": "Nature",
    "science": "Science",
    "cell": "Cell or Frontiers",
    "harvard": "Harvard",
    "chicago": "Chicago author date",
    "ieee": "IEEE",
    "mla": "MLA",
    "mdpi": "MDPI",
    "oup_legacy": "Bioinformatics before 2023 (Oxford author year)",
    "unknown": "not recognised",
}

# Style names used when evaluating style detection, to the family they belong to.
BENCHMARK_FAMILY = {
    "APA 7": "apa", "Nature": "nature", "Science": "science", "Cell": "cell", "Frontiers": "cell",
    "NEJM": "vancouver", "Lancet": "vancouver", "AMA (JAMA, Blood)": "vancouver", "BMJ": "vancouver",
    "Vancouver (NLM)": "vancouver", "PLOS": "vancouver", "Bioinformatics (OUP)": "vancouver",
    "Harvard": "harvard", "Chicago author-date": "chicago", "IEEE": "ieee", "MLA": "mla", "MDPI": "mdpi",
    "Bioinformatics (OUP, before 2023)": "oup_legacy",
}

# Mixed style warning: a second family needs at least this many references and this share of
# the recognised references, in a manuscript with at least MIN_DETECTED recognised references.
MIXED_MIN_REFS = 3
MIXED_MIN_SHARE = 0.10
MIN_DETECTED = 5


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"^\s*(?:\[\d+\]|\(\d+\)|\d{1,3}[.)])\s*(?=\S)", "", t)       # list numbers, also '1.van'
    t = re.split(r"\s(?:doi\s*:|https?://|\(?PMID\s*:?|PMCID\s*:?|Available\s+(?:from|at)|Epub\s|"
                 r"\[(?:published|Epub|cited))", t, maxsplit=1, flags=re.I)[0]
    return t.strip()


def _quoted(t: str):
    return re.search(r"(?:^|(?<=\s))[" + QUOTE_OPEN + r"][^" + QUOTE_CLOSE[:3] + r"]{10,}[" + QUOTE_CLOSE + "]", t)


VANCOUVER_STYLES = ["NEJM or Bioinformatics", "Lancet", "AMA", "BMJ", "PLOS", "Vancouver (NLM)"]


def _tie(names):
    return " / ".join(names)


def _vancouver_style(t: str, pre: str) -> str:
    """Best guess within the Vancouver family; a tie when the marks do not decide."""
    if re.search(r"\b" + YR + r"(?:\s[A-Z][a-z]{2}(?:\s\d{1,2})?)?;\s", t):
        return "Lancet"                                    # '2010; 17: 616'
    if not re.search(r"\.\s$", pre):
        return "NEJM or Bioinformatics"                    # no full stop after the journal
    cands = ["AMA", "BMJ", "PLOS", "Vancouver (NLM)"]
    if re.search(r";" + VOL + r"(?:\s*\([^)]*\))?:\s", t):
        return "PLOS"
    if re.search(r";" + VOL + r"(?:\s*\([^)]*\))?:\S", t):
        cands.remove("PLOS")
    if re.search(r";" + VOL + r"\s*\(", t):
        cands.remove("BMJ")
    rng = re.search(r":\s?[A-Za-z]{0,4}\d+([" + DASHES + r"\-])\d", t)
    if rng and rng.group(1) == "-":
        return "AMA" if "AMA" in cands else _tie(cands)
    if rng:
        cands.remove("AMA")
    return cands[0] if len(cands) == 1 else _tie(cands)


def detect_reference_style(text: str) -> Dict[str, str]:
    """{'family', 'style', 'evidence'} for one reference. 'unknown' when no layout fits.
    'style' is the best guess within the family; ties are written 'A / B'."""
    t = _clean(text)
    out = lambda fam, style, why: dict(family=fam, style=style, evidence=why)   # noqa: E731
    if len(t) < 25:
        return out("unknown", "", "too short")
    # initials before surnames: the first author, or the second when the first has a bare form
    initials_first = bool(re.match(r"^" + INI + r"\s" + SUR, t) or
                          re.match(r"^" + BARE + r",\s" + INI + r"\s" + SUR + r"[,\s]", t))
    q = _quoted(t)

    # quoted titles: Harvard, Chicago, IEEE, MLA
    if q:
        before, after = t[: q.start()], t[q.end():].strip()
        if re.search(r"\(" + YR + r"\)\s*$", before.strip()):
            return out("harvard", "Harvard", "(year) after the authors, quoted title")
        if re.search(r"[.,]\s" + YR + r"\.\s*$", before):
            return out("chicago", "Chicago author date", "year after the authors, quoted title")
        if initials_first:
            return out("ieee", "IEEE", "initials before surnames, quoted title")
        if re.search(r"\bvol\.", after) or re.search(r",\s*" + YR + r"(?:,\s*pp?\.\s*" + PG + r")?\.?$", after):
            return out("mla", "MLA", "quoted title, year after the journal")

    # Vancouver authors ('Surname AB, ...') with the year in brackets after the journal: the older
    # numbered Frontiers layout 'Semin Oncol (2016) 43(6):676-81.', and the form EndNote prints for
    # records imported from PubMed when the output style picks up the country of publication:
    # 'Sci Rep. 15. England(2025). p. 2204.' Both come from one EndNote style in one list.
    vanc_authors = re.match(r"^" + SUR + r"\s" + UP + r"{1,4}(?:,|\.|\set al)", t)
    if vanc_authors and re.search(r"\.\s(?:" + VOL + r"\.\s)?[A-Z][A-Za-z .]*[a-z]\(" + YR + r"\)\.", t):
        return out("vancouver", "EndNote output, 'Journal. Volume. Country(Year).'",
                   "'Surname AB,' authors; 'Volume. Country(Year).' as EndNote prints PubMed records")
    if vanc_authors and re.search(r"[A-Za-z.)]\s\(" + YR + r"\)(?:\s*(?:" + VOL + r"|Volume\s\d)|\s*:|\.?\s*$|\.\s|\s+Epub)", t):
        return out("vancouver", "Frontiers (numbered)", "'Surname AB,' authors; 'Journal (Year) Volume(Issue):Pages'")

    authors_part = t[:80]
    # MDPI: semicolons between authors, 'Journal Year, Vol, Pages' or 'Journal Year.'
    mdpi_tail = re.search(r"\s" + YR + r"(?:,\s*" + VOL + r"(?:\s*\([^)]*\))?(?:,\s*" + PG + r")?)?\.?$", t)
    if mdpi_tail and not re.search(r"\(" + YR + r"\)", t) and \
            (re.search(r";\s" + SUR + r",\s" + INI + r"(?:;|\s)", authors_part) or
             re.match(r"^" + SUR + r",\s" + INI + r"(?:;|\s" + UP + r"[a-z])", t)) and \
            not re.search(r"\b" + YR + r"\s?;", t):
        return out("mdpi", "MDPI", "semicolons between authors, 'Journal Year, Volume, Pages'")

    # Science: initials first, '(Year).' at the end
    if initials_first and re.search(r"\(" + YR + r"\)\.?$", t):
        return out("science", "Science", "initials before surnames, (year) at the end")

    # Bioinformatics before 2023: 'Surname,I.' with no space, '(Year) Title.'
    py = re.search(r"\(" + YR + r"\)\s", t)
    if py and (re.search(r"[a-z" + chr(0xDF) + "-" + chr(0xFF) + r"],(?:" + INI + r")", t[: py.start()]) or
               re.match(r"^" + FIRST + r"(?:\set al\.)?\s\(" + YR + r"\)\s[^.]", t)) and \
            not re.search(r"\(" + YR + r"\)\.", t):
        return out("oup_legacy", "Bioinformatics before 2023", "'Surname,I.' without a space, (year) after authors")

    # Nature: surname first, '(Year).' at the end
    if re.search(r"(?:\s" + VOL + r"(?:\s*\([^)]*\))?(?:,\s*" + PG + r")?,?)?\s*\(" + YR + r"\)\.?$", t) and \
            re.match(r"^" + FIRST, t):
        return out("nature", "Nature", "surname, initials; volume, pages (year) at the end")

    # author then (Year). : APA, Cell, Frontiers
    m = re.search(r"\(" + YR + r"\)\.\s", t)
    if m and re.match(r"^" + FIRST, t):
        auth, tail = t[: m.start()], t[m.end():]
        if re.search(r"\s&\s", auth) or re.search(r",\s*" + VOL + r"(?:\s*\([^)]*\))?,\s*" + PG + r"\.?$", tail):
            return out("apa", "APA", "(year). after the authors, 'Journal, Volume(Issue), Pages'")
        if re.search(r",\sand\s", auth) or re.search(r"[^,]\s" + VOL + r"(?:,\s*" + PG + r")?\.?$", tail):
            unspaced = re.search(UP + r"\." + UP + r"\.", auth)
            spaced = re.search(UP + r"\.\s" + UP + r"\.", auth)
            style = ("Cell" if unspaced and not spaced else "Frontiers" if spaced and not unspaced
                     else _tie(["Cell", "Frontiers"]))
            return out("cell", style, "(year). after the authors, 'Journal Volume, Pages'")
        return out("apa", "APA", "(year). after the authors")

    # Vancouver family: 'Year;Vol(Issue):Pages', 'Year; Vol: Pages' or 'Journal. Year.'
    v = re.search(r"\b(" + YR + r")(?:\s(?:[A-Z][a-z]{2}|Spring|Summer|Autumn|Fall|Winter)(?:\s\d{1,2})?)?\s?;", t)
    if v:
        return out("vancouver", _vancouver_style(t, t[: v.start()]), "'Year;Volume(Issue):Pages'")
    if re.match(r"^" + SUR + r"\s" + UP + r"{1,4}(?:,|\.|\set al)", t) and re.search(r"\b" + YR + r"\b", t):
        # 'Surname AB,' authors without 'Year;Volume': online only or incomplete, the style is not decided
        return out("vancouver", _tie(VANCOUVER_STYLES), "'Surname AB,' authors; no 'Year;Volume' to decide the style")
    return out("unknown", "", "no layout signature")


def manuscript_style(refs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The manuscript's style family, the counts, and a mixed style warning when warranted.

    refs: dicts with 'num' and 'text' (as from extract_references)."""
    per = {r["num"]: detect_reference_style(r.get("text", "")) for r in refs}
    fams = Counter(d["family"] for d in per.values() if d["family"] != "unknown")
    detected = sum(fams.values())
    res: Dict[str, Any] = dict(references=len(refs), detected=detected,
                               unknown=len(refs) - detected,
                               counts={FAMILIES[f]: n for f, n in fams.most_common()},
                               family="", family_label="", share=None, mixed=False, minority=[], message="")
    if not detected:
        res["message"] = "no citation style recognised"
        return res
    top, n_top = fams.most_common(1)[0]
    res.update(family=top, family_label=FAMILIES[top], share=round(n_top / detected, 3))
    res["style_guess"] = _pooled_style([d for d in per.values() if d["family"] == top],
                                       [r.get("text", "") for r in refs if per[r["num"]]["family"] == top])
    others = [(f, n) for f, n in fams.most_common() if f != top
              and n >= MIXED_MIN_REFS and n / detected >= MIXED_MIN_SHARE]
    if detected >= MIN_DETECTED and others:
        res["mixed"] = True
        res["minority"] = [dict(family=FAMILIES[f], references=sorted(k for k, d in per.items() if d["family"] == f))
                           for f, _ in others]
        parts = "; ".join(f"{m['family']}: references {_ranges(m['references'])}" for m in res["minority"])
        res["message"] = (f"References are in mixed citation styles. Most are {FAMILIES[top]} "
                          f"({n_top} of {detected} recognised), but {parts}. References copied from different "
                          f"sources or pasted from a generator often look like this. Check that these were "
                          f"taken from the cited papers; mixed styles alone do not show that a reference is wrong.")
    return res


def _pooled_style(dets: List[Dict[str, str]], texts: List[str]) -> str:
    """The exact style most references agree with. A reference that ties ('A / B') supports
    each tied style. BMJ is the Vancouver layout without issue numbers, so when not one of five
    or more references gives an issue, BMJ is preferred over Vancouver (NLM)."""
    votes: Counter = Counter()
    for d in dets:
        for name in (d["style"] or "").split(" / "):
            if name:
                votes[name] += 1
    if not votes:
        return ""
    best = max(votes.values())
    top = [k for k, v in votes.items() if v == best]
    if len(top) > 1 and {"BMJ", "Vancouver (NLM)"} <= set(top) and len(texts) >= 5 and \
            not any(re.search(r";\s?" + VOL + r"\s*\(", t) for t in texts):
        return "BMJ"
    return top[0] if len(top) == 1 else _tie(sorted(top))


def _ranges(nums: List[int]) -> str:
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(out)
