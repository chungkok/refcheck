"""Small text helpers shared by the extractor and the matcher."""

import re
import unicodedata
from difflib import SequenceMatcher

DASHES = "‐‑‒–—−"
DASH_RE = "[-" + DASHES + "]"

_SPELLING = {
    "tumours": "tumors", "tumour": "tumor", "paediatric": "pediatric",
    "paediatrics": "pediatrics", "centre": "center", "centres": "centers",
    "behaviour": "behavior", "organisation": "organization",
    "organisations": "organizations", "haematological": "hematological",
    "haematology": "hematology", "haematopoietic": "hematopoietic",
    "leukaemia": "leukemia", "leukaemias": "leukemias", "oesophageal": "esophageal",
    "anaemia": "anemia", "characterisation": "characterization",
    "randomised": "randomized", "optimised": "optimized",
}


def norm_dashes(s):
    for d in DASHES:
        s = s.replace(d, "-")
    return s


def clean_text(s):
    """Lower case ASCII words separated by single spaces."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = norm_dashes(s).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_normalize(s):
    return " ".join(_SPELLING.get(w, w) for w in clean_text(s).split())


def compact(s):
    return re.sub(r"[^a-z0-9]", "", clean_text(s))


def similarity(a, b):
    a, b = title_normalize(a), title_normalize(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def token_overlap(a, b):
    aa, bb = set(title_normalize(a).split()), set(title_normalize(b).split())
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def extract_year(s):
    m = re.search(r"\b(?:19|20)\d{2}\b", str(s or ""))
    return m.group(0) if m else ""


def first_author_surname(authors):
    """'Patel A, Dogan H' -> 'patel';  'von Deimling A' -> 'von deimling'."""
    if not authors:
        return ""
    first = re.split(r";|,|\bet al\b|\band\b|&", authors, flags=re.I)[0].strip()
    first = re.sub(r"^\d+[.)]\s*", "", first)
    # Drop trailing initials ("Patel A", "Brat DJ", "Smith J.R.")
    parts = first.split()
    while len(parts) > 1 and re.fullmatch(r"(?:[A-Z]\.?){1,4}", parts[-1]):
        parts.pop()
    return clean_text(" ".join(parts))


def display_surname(authors):
    """Like first_author_surname but keeps original capitalisation."""
    if not authors:
        return ""
    first = re.split(r";|,|\bet al\b|\band\b|&", authors, flags=re.I)[0].strip()
    parts = first.split()
    while len(parts) > 1 and re.fullmatch(r"(?:[A-Z]\.?){1,4}", parts[-1]):
        parts.pop()
    return " ".join(parts).strip(" .")


def first_page(p):
    """'609-612' -> '609', 'iv1-iv66' -> 'iv1', 'e123' -> 'e123'."""
    if not p:
        return ""
    first = re.split(r"\s*" + DASH_RE + r"\s*", str(p).strip())[0]
    return clean_text(first).replace(" ", "")


def expand_pages(p):
    """MEDLINE abbreviates page ranges: '1283-94' -> ('1283', '1294')."""
    p = norm_dashes(str(p or "")).strip()
    if "-" not in p:
        return p, ""
    sp, ep = [x.strip() for x in p.split("-", 1)]
    if sp.isdigit() and ep.isdigit() and len(ep) < len(sp):
        ep = sp[: len(sp) - len(ep)] + ep
    else:
        m1, m2 = re.match(r"([A-Za-z]*)(\d+)$", sp), re.match(r"([A-Za-z]*)(\d+)$", ep)
        if m1 and m2 and not m2.group(1) and len(m2.group(2)) < len(m1.group(2)):
            d = m1.group(2)
            ep = m1.group(1) + d[: len(d) - len(m2.group(2))] + m2.group(2)
    return sp, ep
