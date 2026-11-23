"""Evidence based scoring: field levels, the weights model, fitting, and evidence mode end to end."""
import json
import math
import random
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

from fixtures import ARTICLES, MockClient  # noqa: E402
from refcheck.config import Settings  # noqa: E402
from refcheck.evidence import FIELDS, WeightModel, compare_fields, model_for, points_from_levels  # noqa: E402
from refcheck.extract import parse_reference_text  # noqa: E402
from refcheck.pubmed import decide, match_reference, score_candidate  # noqa: E402

ROBIN = ("Deacon S, Cahyani I, Holmes N, Fox G, Munro R, Wibowo S, et al. ROBIN: A unified nanopore-based assay "
         "integrating intraoperative methylome classification and next-day comprehensive profiling for "
         "ultra-rapid tumor diagnosis. Neuro Oncol. 2025;27(8):2035-2046.")


def cands():
    c = MockClient()
    import refcheck.pubmed as pm
    return [r for a in ARTICLES for r in pm.parse_pubmed_xml(
        f"<PubmedArticleSet>{__import__('fixtures').article_xml(a)}</PubmedArticleSet>")], c


def variants():
    t = ROBIN
    return [t, t.replace("2025", "2024"), t.replace("Deacon S", "Deakin S"), t.replace("Neuro Oncol", "Cancer"),
            t.replace("2035-2046", "999-1000"), t + " doi:10.1093/neuonc/noaf103", t + " PMID: 40392954",
            t.replace("ROBIN: A unified", "Totally different study of"), "Smith J. Unrelated. Blood. 2020;1:1-2."]


def test_levels_rebuild_the_hand_set_points_exactly():
    recs, _ = cands()
    n = 0
    for text in variants():
        ref = parse_reference_text(text)
        for rec in recs:
            lv = compare_fields(ref, rec)
            assert set(lv) == set(FIELDS) and all(lv[f] in FIELDS[f] for f in lv)
            assert points_from_levels(lv) == score_candidate(ref, rec)[0], (text, rec["pmid"], lv)
            n += 1
    assert n >= 18


def weights_file(tmp_path, **th):
    w = {f: {lv: 0.0 for lv in FIELDS[f] if lv != "missing"} for f in FIELDS}
    w["title"].update(t98=4.0, t94=3.0, low=-6.0)
    w["first_author"].update(exact=4.0, differs=-4.0)
    w["year"].update(same=2.0, off_more=-5.0)
    w["journal"].update(exact=2.0)
    w["doi"].update(agree=5.0, differs=-3.0)
    data = {"format": "refcheck-weights/1", "weights": w, "prior_log2_odds": -3.0,
            "calibration": {"a": math.log(2), "b": 0.0}, "thresholds": th or {}}
    p = tmp_path / "weights.json"
    p.write_text(json.dumps(data))
    return p


def test_weight_model_scores_explains_and_ignores_missing(tmp_path):
    m = WeightModel.load(weights_file(tmp_path))
    lv = dict(title="t98", first_author="exact", year="same", journal="missing", volume="missing",
              first_page="missing", doi="missing", pmid="missing")
    assert m.log2_odds(lv) == -3.0 + 4 + 4 + 2
    assert abs(m.probability(7.0) - 2 ** 7 / (1 + 2 ** 7)) < 1e-9          # a = ln 2: probability from log2 odds
    assert "title t98 +4.0" in m.explain(lv) and "journal" not in m.explain(lv)
    with pytest.raises(ValueError):
        WeightModel({"format": "other"})
    with pytest.raises(ValueError, match="needs a weights file"):
        model_for(Settings(cache_path="", scoring="evidence"))
    assert model_for(Settings(cache_path="")) is None


def test_evidence_mode_end_to_end(tmp_path):
    st = Settings(cache_path="", scoring="evidence", weights_path=str(weights_file(tmp_path, auto_probability=0.99)))
    c = MockClient(settings=st)
    res = match_reference(c, parse_reference_text(ROBIN))
    assert res["scoring"] == "evidence" and res["status"] == "AUTO_MATCH"
    assert res["best"]["pmid"] == "40392954" and res["best"]["probability"] > 0.99
    assert "weights_explained" in res["best"] and res["best"]["points"] > 0
    assert any(x["pmid"] == "40392954" and x["levels"]["title"] == "t98" for x in res["candidates"])
    # a wrong year more than one apart: probability falls below the automatic threshold
    res = match_reference(c, parse_reference_text(ROBIN.replace("2025;27", "2019;27")))
    assert res["status"] != "AUTO_MATCH"


def test_points_mode_still_records_candidates():
    res = match_reference(MockClient(), parse_reference_text(ROBIN))
    assert res["scoring"] == "points" and "probability" not in res["best"]
    assert res["candidates"] and all("levels" in x and "points" in x for x in res["candidates"])


def test_different_title_blocks_an_automatic_match_unless_both_identifiers_agree():
    s = Settings(cache_path="")
    lv = dict(title="low", first_author="exact", year="same", journal="exact", volume="agree", first_page="agree",
              doi="agree", pmid="missing")
    best = dict(pmid="1", score=96, title_similarity=0.3, match_reasons="title-differs;first-author;doi", levels=lv)
    st, why = decide(best, None, s)
    assert st == "REVIEW" and any("title differs from the record" in w for w in why)
    best["levels"] = dict(lv, pmid="agree")
    best["match_reasons"] += ";pmid-in-text"
    assert decide(best, None, s)[0] == "AUTO_MATCH"


# ---------------------------------------------------------------- fitting --

def synthetic(n_refs=600, seed=3):
    """References whose right candidate shows each level with known m, wrong ones with known u."""
    rng = random.Random(seed)
    m = {"title": {"t98": 0.9, "t94": 0.08, "low": 0.02}, "first_author": {"exact": 0.95, "differs": 0.05},
         "year": {"same": 0.9, "off1": 0.08, "off_more": 0.02}}
    u = {"title": {"t98": 0.02, "t94": 0.08, "low": 0.9}, "first_author": {"exact": 0.1, "differs": 0.9},
         "year": {"same": 0.3, "off1": 0.2, "off_more": 0.5}}

    def draw(p):
        r, acc = rng.random(), 0
        for k, v in p.items():
            acc += v
            if r < acc:
                return k
        return k

    def levels(table):
        lv = {f: "missing" for f in FIELDS}
        lv.update({f: draw(table[f]) for f in table})
        return lv
    recs = []
    for i in range(n_refs):
        cs = [dict(pmid=f"g{i}", levels=levels(m), points=0)] + \
             [dict(pmid=f"x{i}_{k}", levels=levels(u), points=0) for k in range(3)]
        recs.append(dict(ref_id=f"r{i}", source="genuine", gold_pmid=f"g{i}", candidates=cs, live=True))
    return recs, m, u




def test_cli_evidence_options(tmp_path, monkeypatch, capsys):
    import refcheck_cli
    monkeypatch.setattr(refcheck_cli, "PubMedClient", lambda *a, **k: MockClient(settings=k.get("settings")))
    src = tmp_path / "refs.txt"
    src.write_text("References\n1. " + ROBIN + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="needs a weights file"):
        refcheck_cli.main([str(src), "--email", "a@b.c", "--scoring", "evidence", "--sources", "none"])
    w = weights_file(tmp_path)
    assert refcheck_cli.main([str(src), "--email", "a@b.c", "--outdir", str(tmp_path / "o"), "--sources", "none",
                              "--scoring", "evidence", "--weights", str(w)]) == 0
    assert "Scoring: evidence weights from weights.json" in capsys.readouterr().out
    rep = json.loads(next((tmp_path / "o").glob("*_integrity_report.json")).read_text())
    auto = rep["references"][0]["automatic"]
    assert auto["scoring"] == "evidence" and auto["probability"] > 0.99 and auto["candidates"]
