"""Build a small test manuscript covering the citation forms the annotator handles."""
import sys
from docx import Document

REFS = [
    "Price M, Ballard CAP, Benedetti JR, Kruchko C, Barnholtz-Sloan JS, Ostrom QT. CBTRUS Statistical Report: Primary Brain and Other Central Nervous System Tumors Diagnosed in the United States in 2018-2022. Neuro Oncol. 2025;27(Supplement_4):iv1-iv66.",
    "Patel A, Dogan H, Payne A, Krause E, Sievers P, Schoebe N, et al. Rapid-CNS2: rapid comprehensive adaptive nanopore-sequencing of CNS tumors, a proof-of-concept study. Acta Neuropathol. 2022;143(5):609-612.",
    "Deacon S, Cahyani I, Holmes N, Fox G, Munro R, Wibowo S, et al. ROBIN: A unified nanopore-based assay integrating intraoperative methylome classification and next-day comprehensive profiling for ultra-rapid tumor diagnosis. Neuro Oncol. 2025;27(8):2035-2046.",
    "Brat DJ, Aldape K, French PJ, Louis DN, Nasrallah MP, Reifenberger G, et al. cIMPACT-NOW Update 12: Refining pathology-based risk stratification and grading for IDH-mutant gliomas. Neuro Oncol. 2026;28(9):2069-2089.",
    "Yuan D, Jugas R, Pokorna P, Sterba J, Slaby O, Schmid S, et al. crossNN is an explainable framework for cross-platform DNA methylation-based classification of tumors. Nat Cancer. 2025;6(7):1283-1294.",
    "Ontario Health (Quality). DNA Methylation-Based Classification for Central Nervous System Tumours: A Health Technology Assessment. Ont Health Technol Assess Ser. 2025;25(5):1-93.",
    "Halldorsson S, Nagymihaly RM, Domilongo Bope C, et al. Whole-genome long-read sequencing for rapid comprehensive molecular diagnostics of brain tumors. medRxiv. 2026. doi:10.64898/2026.04.23.26351563.",
    "Patel A, Dogan H, Payne A, et al. Rapid-CNS2: rapid comprehensive adaptive nanopore sequencing of CNS tumours, a proof of concept study. Acta Neuropathol. 2022;143(5):609-12.",
]


def build(path, word_numbering=False):
    d = Document()
    d.add_heading("Test manuscript", 0)
    d.add_paragraph("Brain tumours are common [1]. Nanopore methods are fast [2,3] and cheap [2-5].")
    p = d.add_paragraph("Intraoperative classification is feasible")
    r = p.add_run("3,4"); r.font.superscript = True
    p.add_run(". We seeded 10")
    r = p.add_run("6"); r.font.superscript = True
    p.add_run(" cells per well and a cohort of mm")
    r = p.add_run("2"); r.font.superscript = True
    p.add_run(" areas. Preprints exist [7] and a split ")
    p.add_run("[1,"); p.add_run(" 6]"); p.add_run(" citation. Out of range [12]. Duplicate paper [8].")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Assay"
    t.cell(0, 1).text = "crossNN [5]"
    d.add_heading("References", 1)
    for i, ref in enumerate(REFS, 1):
        if word_numbering:
            d.add_paragraph(ref, style="List Number")
        else:
            d.add_paragraph(f"{i}. {ref}")
    d.add_heading("Figure legends", 1)
    d.add_paragraph("Figure 1. Workflow adapted from [2].")
    d.save(path)


if __name__ == "__main__":
    build(sys.argv[1], word_numbering=len(sys.argv) > 2)
