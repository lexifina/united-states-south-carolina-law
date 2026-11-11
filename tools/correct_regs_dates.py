"""Recheck official regulation PDFs and emit a new date correction overlay.

Legal text is copied only from unchanged official PDF text layers. The earlier
part is read only; no date is inferred from the listing's currency date.
"""
import argparse, bisect, datetime as dt, hashlib, json, re
from pathlib import Path
import pymupdf
from parse_regs import chapter_date, EFFECTIVE, CURRENCY_DATE

HERE = Path(__file__).resolve().parent
def latest_date(pages):
    chosen = chapter_date(pages)
    candidates = [chosen] if chosen else []
    text = '\n'.join(pages)
    starts = []; offset = 0
    for p in pages:
        starts.append(offset); offset += len(p) + 1
    for m in EFFECTIVE.finditer(text):
        try:
            day = dt.datetime.strptime(m.group(1).replace(',', ''), '%B %d %Y').date().isoformat()
        except ValueError:
            continue
        if day > CURRENCY_DATE:
            continue
        before = text[max(0, m.start()-200):m.start()]
        after = text[m.end():m.end()+800]
        if re.search(r'\b\d{4} Act No\.\s*\d+', before) and 'are repealed' in after:
            basis = 'explicit statutory repeal in chapter editorial note'
        elif re.search(r'\(1\)\s*$', before) and re.match(r',\s*(?:eighty-three|must have|two hundred|three hundred)', after):
            basis = 'explicit commencement of appraisal education requirements'
        else:
            continue
        candidates.append({'date': day, 'basis': basis,
            'pdf_page': bisect.bisect_right(starts, m.start()),
            'quote': ' '.join(text[max(0,m.start()-200):m.end()+800].split())})
    return max(candidates,key=lambda x:x['date']) if candidates else None

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-dir',type=Path,required=True)
    ap.add_argument('--base',type=Path,default=HERE/'corpus.d/90-regulations.json')
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--records',type=Path,required=True)
    a=ap.parse_args()
    if Path(str(a.out)+'.PUBLISH').exists():
        raise SystemExit('Refuse editing a published part')
    base=json.loads(a.base.read_text(encoding='utf8'))
    changes=[]; evidence=[]
    for act in base['acts']:
        cid=int(act['slug'].split('-')[-1])
        pdf=a.source_dir/f'reg-{cid}.pdf'
        data=pdf.read_bytes()
        with pymupdf.open(stream=data,filetype='pdf') as doc:
            pages=[p.get_text() for p in doc]
        v=act['versions'][0]
        assert '\n'.join(pages)==v['units']['text']['text'], f'Source changed: {cid}'
        assert hashlib.sha256(data).hexdigest()==v['sha256'], f'Source hash changed: {cid}'
        date=latest_date(pages)
        assert date is not None,cid
        if date['date'] != v['from']:
            evidence.append({'chapter':cid,'old_date':v['from'],'url':act['url'],
                'source_sha256':v['sha256'],**date})
            v['from']=date['date']
            v['note']=f"In-force basis: {date['basis']}; official PDF page {date['pdf_page']}."
            changes.append(act)
    part={'corrects':a.base.name,'acts':changes,'laws':{},'sources':[],'applies':[],
        'decisions':['Current regulation chapter dates include explicit later statutory repeals and commenced appraisal education requirements. Chapter 139 uses 3 May 2018 (2018 Act No. 163); chapter 137 uses 1 January 2026. Complete official PDF text layers remain unchanged. Expired temporary emergency measures and the conditional 1 January 2027 clause do not date the present chapter texts.'],
        'ai_use':['GPT-6 inspected source date contexts and wrote the deterministic metadata correction script; no model wrote or changed legal text.']}
    a.out.parent.mkdir(exist_ok=True,parents=True)
    a.out.write_text(json.dumps(part,ensure_ascii=False,sort_keys=True,indent=1)+'\n',encoding='utf8',newline='\n')
    a.records.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'corrections':len(changes),'chapters':[r['chapter'] for r in evidence],
        'part_sha256':hashlib.sha256(a.out.read_bytes()).hexdigest()}))
if __name__=='__main__': main()
