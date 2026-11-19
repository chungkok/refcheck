"""Side by side comparison of a parsed reference and a PubMed record."""

from .textutil import (clean_text, compact, first_author_surname, first_page, norm_dashes,
                       similarity)


def _same(ok):
    return "✅" if ok is True else ("❌" if ok is False else "–")


def compare_fields(ref, cand):
    """Rows of (field, your document, PubMed, verdict) for the review screen."""
    rows = []
    ts = similarity(ref["title"], cand["title"])
    rows.append(("Title", ref["title"], cand["title"],
                 f"{_same(ts >= 0.94 if ref['title'] else None)} {round(100 * ts)}%"))

    ra, ca = first_author_surname(ref["authors"]), clean_text(cand.get("first_author", ""))
    rows.append(("First author", ref["authors"].split(",")[0] if ref["authors"] else "",
                 cand.get("first_author", ""),
                 _same((ra.replace(" ", "") == ca.replace(" ", "")) if ra and ca else None)))

    rj = compact(ref["journal"])
    cjs = [compact(cand.get("journal", "")), compact(cand.get("iso_journal", ""))]
    rows.append(("Journal", ref["journal"], cand.get("iso_journal") or cand.get("journal", ""),
                 _same((rj in cjs or any(c and (rj in c or c in rj) for c in cjs)) if rj else None)))

    for label, k in (("Year", "year"), ("Volume", "volume"), ("Issue", "issue")):
        a, b = str(ref.get(k, "") or ""), str(cand.get(k, "") or "")
        rows.append((label, a, b, _same(clean_text(a) == clean_text(b) if a and b else None)))

    rp, cp = first_page(ref["pages"]), first_page(cand.get("pages", ""))
    rows.append(("Pages", norm_dashes(ref["pages"] or ""), cand.get("pages", ""),
                 _same(rp == cp if rp and cp else None)))

    rd, cd = (ref.get("doi") or "").lower().strip("."), (cand.get("doi") or "").lower()
    rows.append(("DOI", ref.get("doi", ""), cand.get("doi", ""), _same(rd == cd if rd and cd else None)))
    return rows
