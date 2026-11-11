"""Parse cached official South Carolina regulation PDFs into one append-only part.

The complete PDF text layer for each chapter is kept as one unit. Chapters with
pages lacking text are held for manual review; no OCR or model rewriting occurs.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
JOB = HERE.parent / "gpt-workers" / "jobs" / "q-1507"
EVIDENCE = JOB / "evidence"
BASE = "https://www.scstatehouse.gov/coderegs/Chapter%20{}.pdf"
DATE = "2026-03-27"


def main() -> None:
    listing = (EVIDENCE / "regulations-list.html").read_bytes()
    ids = sorted({int(x) for x in re.findall(rb'/coderegs/Chapter (\d+)\.pdf', listing)})
    acts, held, image_rows = [], [], []
    for cid in ids:
        pdf = EVIDENCE / f"reg-{cid}.pdf"
        if not pdf.is_file():
            held.append({"chapter": cid, "reason": "PDF not fetched"})
            continue
        data = pdf.read_bytes()
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
            pages = [page.get_text() for page in doc]
            no_text = [i + 1 for i, text in enumerate(pages) if not text.strip()]
            images = [(i + 1, len(page.get_images(full=True))) for i, page in enumerate(doc) if page.get_images(full=True)]
            if no_text:
                held.append({"chapter": cid, "reason": "pages without text layer", "pages": no_text})
                continue
            text = "\n".join(pages)
            if not text.startswith(f"CHAPTER {cid}\n"):
                held.append({"chapter": cid, "reason": "chapter heading mismatch", "first": text[:100]})
                continue
            heading = next((line.strip() for line in pages[0].splitlines()[1:] if line.strip()), f"Chapter {cid}")
            url = BASE.format(cid)
            slug = f"regulation-ch-{cid:03d}"
            acts.append({"slug": slug, "short": f"S.C. Regs. ch. {cid}",
                         "title": f"South Carolina Code of Regulations Chapter {cid}: {heading}",
                         "url": url, "first_law": None, "earlier_laws": [],
                         "versions": [{"from": DATE, "source_url": url,
                                       "sha256": hashlib.sha256(data).hexdigest(),
                                       "units": {"text": {"label": f"Chapter {cid} complete PDF text layer",
                                                          "kind": "section", "text": text}}}]})
            for page, count in images:
                image_rows.append((slug, DATE, url, f"PDF page {page}: {count} source image(s)"))
        except Exception as exc:
            held.append({"chapter": cid, "reason": f"parse error: {exc}"})
    part = {"sources": [{"name": "South Carolina Legislative Council, unannotated Code of Regulations",
                          "url": "https://www.scstatehouse.gov/coderegs/statmast.php",
                          "retrieved": "2026-11-11",
                          "licence": "The unannotated South Carolina Code of Regulations, consisting only of Regulation text, numbering, and history, may be copied from this website at the reader's expense and effort without need for permission.",
                          "notes": "Current through State Register Volume 50, Issue 3, effective March 27, 2026."}],
            "acts": acts, "laws": {}, "applies": [],
            "decisions": ["Each chapter is an oldest-version-available current text from the Legislative Council PDF text layer, kept as one file. The source states the Code is current through State Register Volume 50, Issue 3, effective March 27, 2026; that source currency date is used for this version. No OCR or model text edits were made."],
            "ai_use": ["none"]}
    out = HERE / "corpus.d" / "90-regulations.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(part, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf8", newline="\n")
    (JOB / "regulations-held.json").write_text(json.dumps(held, indent=2) + "\n", encoding="utf8")
    with (JOB / "images-deferred.tsv").open("w", encoding="utf8", newline="\n") as f:
        f.write("act\tversion\tofficial image URL\tposition\n")
        for row in image_rows:
            f.write("\t".join(map(str, row)) + "\n")
    print(json.dumps({"listed": len(ids), "acts": len(acts), "held": len(held), "image_pages": len(image_rows)}))


if __name__ == "__main__":
    main()
