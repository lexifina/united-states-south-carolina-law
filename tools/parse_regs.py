"""Parse cached official South Carolina regulation PDFs into one append-only part.

The complete PDF text layer for each chapter is kept as one unit. Chapters with
pages lacking text are held for manual review; no OCR or model rewriting occurs.
"""
from __future__ import annotations

import hashlib
import json
import re
import bisect
import datetime as dt
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
JOB = HERE.parent / "gpt-workers" / "jobs" / "q-1507"
EVIDENCE = JOB / "evidence"
BASE = "https://www.scstatehouse.gov/coderegs/Chapter%20{}.pdf"
CURRENCY_DATE = "2026-03-27"
MONTH = r"(?:January|February|March|April|May|June|July|August|September|Sept\.?|October|November|December)"
EFFECTIVE = re.compile(rf"\b(?:became\s+)?eff(?:ective)?\.?\s+(?:on\s+)?({MONTH}\s+\d{{1,2}},?\s+\d{{4}})", re.I)
SECTION = re.compile(r"(?m)^\s*\d+[–-]\d+[A-Za-z0-9.()–-]*\.\s")
HISTORY = re.compile(r"\bHISTORY\s*:", re.I)
NOTE = re.compile(r"\bEditor[’']s\s+Note\b|\bSee\s+(?:SCSR|Executive\s+Order)\b", re.I)


def chapter_date(pages: list[str]) -> dict | None:
    """Latest dated HISTORY amendment, or explicit chapter commencement note.

    Notes about temporary emergency rules and dates inside substantive rules do
    not date the whole chapter's consolidated text.
    """
    starts = []
    offset = 0
    for page in pages:
        starts.append(offset)
        offset += len(page) + 1
    text = "\n".join(pages)
    sections = [m.start() for m in SECTION.finditer(text)]
    histories = [m.start() for m in HISTORY.finditer(text)]
    notes = [m.start() for m in NOTE.finditer(text)]
    found = []
    for match in EFFECTIVE.finditer(text):
        s = re.sub(r"\bSept\.?\b", "September", match.group(1)).replace(",", "")
        try:
            day = dt.datetime.strptime(s, "%B %d %Y").date().isoformat()
        except ValueError:
            continue
        if day > CURRENCY_DATE:
            continue
        prev_section = max((p for p in sections if p < match.start()), default=-1)
        prev_history = max((p for p in histories if p < match.start()), default=-1)
        prev_note = max((p for p in notes if p < match.start()), default=-1)
        if match.group(0).lower().startswith("became effective"):
            basis = "explicit chapter commencement"
        elif prev_history > max(prev_section, prev_note):
            basis = "HISTORY effective amendment"
        else:
            continue
        found.append({"date": day, "basis": basis,
                      "pdf_page": bisect.bisect_right(starts, match.start()),
                      "quote": " ".join(text[max(0, match.start() - 100):match.end() + 45].split())})
    return max(found, key=lambda r: r["date"]) if found else None


def main() -> None:
    listing = (EVIDENCE / "regulations-list.html").read_bytes()
    ids = sorted({int(x) for x in re.findall(rb'/coderegs/Chapter (\d+)\.pdf', listing)})
    acts, held, image_rows, date_evidence = [], [], [], []
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
            date = chapter_date(pages)
            if date is None:
                held.append({"chapter": cid, "reason": "no chapter-specific effective or amendment date in PDF"})
                continue
            heading = next((line.strip() for line in pages[0].splitlines()[1:] if line.strip()), f"Chapter {cid}")
            url = BASE.format(cid)
            slug = f"regulation-ch-{cid:03d}"
            date_evidence.append({"chapter": cid, "url": url, "source_sha256": hashlib.sha256(data).hexdigest(), **date})
            acts.append({"slug": slug, "short": f"S.C. Regs. ch. {cid}",
                         "title": f"South Carolina Code of Regulations Chapter {cid}: {heading}",
                         "url": url, "first_law": None, "earlier_laws": [],
                         "versions": [{"from": date["date"], "source_url": url,
                                       "sha256": hashlib.sha256(data).hexdigest(),
                                       "units": {"text": {"label": f"Chapter {cid} complete PDF text layer",
                                                          "kind": "section", "text": text}}}]})
            for page, count in images:
                image_rows.append((slug, date["date"], url, f"PDF page {page}: {count} source image(s)"))
        except Exception as exc:
            held.append({"chapter": cid, "reason": f"parse error: {exc}"})
    part = {"sources": [{"name": "South Carolina Legislative Council, unannotated Code of Regulations",
                          "url": "https://www.scstatehouse.gov/coderegs/statmast.php",
                          "retrieved": "2026-11-11",
                          "licence": "The unannotated South Carolina Code of Regulations, consisting only of Regulation text, numbering, and history, may be copied from this website at the reader's expense and effort without need for permission.",
                          "notes": "Current through State Register Volume 50, Issue 3, effective March 27, 2026."}],
            "acts": acts, "laws": {}, "applies": [],
            "decisions": ["Each chapter is an oldest-version-available current text from the Legislative Council PDF text layer, kept as one file. Each version is dated by the chapter PDF's latest explicit HISTORY amendment effective date, or by an explicit chapter commencement note. The listing-wide March 27, 2026 currency date is not used as a version date. Chapters without a chapter-specific date are held. No OCR or model text edits were made."],
            "ai_use": ["none"]}
    out = HERE / "corpus.d" / "90-regulations.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(part, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf8", newline="\n")
    (JOB / "regulations-held.json").write_text(json.dumps(held, indent=2) + "\n", encoding="utf8")
    (JOB / "date-evidence.json").write_text(json.dumps(date_evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    with (JOB / "images-deferred.tsv").open("w", encoding="utf8", newline="\n") as f:
        f.write("act\tversion\tofficial image URL\tposition\n")
        for row in image_rows:
            f.write("\t".join(map(str, row)) + "\n")
    print(json.dumps({"listed": len(ids), "acts": len(acts), "held": len(held), "image_pages": len(image_rows)}))


if __name__ == "__main__":
    main()
