import csv, json, re, math
from pathlib import Path
from datetime import datetime, date, timedelta
from difflib import SequenceMatcher

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
POLICY_FILE=ROOT/'data'/'policy_v5.json'

def readcsv(path):
    with open(path, newline='', encoding='utf-8-sig') as f: return list(csv.DictReader(f))
def dt(s):
    if not s: return None
    for fmt in ('%Y-%m-%d %H:%M','%Y-%m-%d','%Y-%m-%dT%H:%M:%S'):
        try:return datetime.strptime(s.strip(),fmt)
        except:pass
    return None
def norm(s):
    t=re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
    # Common procurement abbreviations explicitly illustrated in the worked examples.
    t=re.sub(r'\bbrg\b','bearing',t); t=re.sub(r'\bbear\b','bearing',t); t=re.sub(r'\bhyd\b','hydraulic',t); t=re.sub(r'\bvci\b','vci',t)
    return t
def load_data():
    items=readcsv(DATA/'master_data/item_master.csv'); suppliers=readcsv(DATA/'master_data/supplier_master.csv'); pos=readcsv(DATA/'master_data/po_history.csv')
    plants=readcsv(DATA/'master_data/plants.csv'); regs=readcsv(DATA/'contracts/contract_register.csv'); lines=readcsv(DATA/'contracts/contract_lines.csv')
    # Signed PDFs after register's 31-Oct update are binding and begin with zero consumption.
    for p in (DATA/'contracts/pdf').glob('*.txt'):
        txt=p.read_text(errors='ignore'); cid=re.search(r'Contract No\.\s*(RC-[\w-]+)',txt); sid=re.search(r'Supplier ID\s*(S\d+)',txt)
        term=re.search(r'valid from (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})',txt,re.I); cap=re.search(r'not exceed INR ([\d,]+)',txt,re.I)
        if not (cid and sid and term and cap): continue
        if any(x['contract_id']==cid.group(1) for x in regs): continue
        sm=next((x for x in suppliers if x['supplier_id']==sid.group(1)),{})
        regs.append(dict(contract_id=cid.group(1),supplier_id=sid.group(1),supplier_name=sm.get('name',''),start_date=term.group(1),end_date=term.group(2),value_cap=cap.group(1).replace(',',''),consumed_value='0'))
        # Read item/rate schedule lines from PDF text.
        for row in re.findall(r'(ITM-\d+)\s+(.+?)\s+(NOS|KG|LTR|MTR|PAIR|BRL|BKT|MON|JOB)\s+([\d,]+\.\d{2})',txt):
            lines.append(dict(contract_id=cid.group(1),item_code=row[0],description=row[1].strip(),uom=row[2],rate=row[3].replace(',','')))
    return items,suppliers,pos,regs,lines,plants

def match_item(line,items):
    code=(line.get('item_code') or '').strip()
    if code:
        found=next((i for i in items if i['item_code'].lower()==code.lower()),None)
        if found:return found
    q=norm(line.get('description'))
    if not q:return None
    # token overlap + sequence score; avoids confidently mapping unrelated vague text.
    best=None; score=0
    for i in items:
        d=norm(i['description']); seq=SequenceMatcher(None,q,d).ratio(); qt=set(q.split()); dtok=set(d.split()); overlap=len(qt&dtok)/max(1,len(qt))
        sc=max(seq,overlap*.92)
        if sc>score:best,score=i,sc
    return best if score>=.48 else None

def ranking(item_code,asof,items,suppliers,pos,regs,lines):
    if not item_code:return []
    cutoff=asof-timedelta(days=548); supmap={s['supplier_id']:s for s in suppliers}; scores=[]
    for sid,s in supmap.items():
        if s['status'].lower()!='active':continue
        hist=[p for p in pos if p['supplier_id']==sid and p['item_code']==item_code and dt(p['po_date']) and cutoff<=dt(p['po_date'])<=asof]
        ctr=[r for r in regs if r['supplier_id']==sid and any(l['contract_id']==r['contract_id'] and l['item_code']==item_code for l in lines) and dt(r['start_date'])<=asof]
        if not hist and not ctr:continue
        recent=max([dt(p['po_date']) for p in hist],default=None)
        rec=1 if recent and (asof-recent).days<=90 else (.5 if recent and (asof-recent).days<=365 else 0)
        ontime=float(s.get('on_time_pct') or 0)/100; quality=float(s.get('quality_rating') or 0)/5
        # Explainable weighted score: frequency 35%, recency 25%, contract 15%, delivery 15%, quality 10%.
        score=min(len(hist),10)/10*.35+rec*.25+(0.15 if ctr else 0)+ontime*.15+quality*.10
        lastpo=max(hist,key=lambda p:dt(p['po_date'])) if hist else None
        lastcontract=max(ctr,key=lambda r:dt(r['start_date'])) if ctr else None
        price=float(lastpo['unit_price']) if lastpo else (float(next(l['rate'] for l in lines if l['contract_id']==lastcontract['contract_id'] and l['item_code']==item_code)) if lastcontract else 0)
        scores.append(dict(supplier_id=sid,name=s['name'],score=score,po_count=len(hist),recency=rec,contract=bool(ctr),on_time=ontime,quality=quality,reference_price=price,last_po=lastpo['po_number'] if lastpo else '',status=s['status']))
    return sorted(scores,key=lambda x:(-x['score'],-x['po_count'],x['supplier_id']))

def process(rows,policy=None):
    items,suppliers,pos,regs,clines,plants=load_data(); policy=policy or json.loads(POLICY_FILE.read_text())
    policy_versions=[policy]
    histdir=DATA/'policies'
    if histdir.exists():
        for pf in histdir.glob('policy_*.json'):
            try:
                pp=json.loads(pf.read_text())
                if pp.get('version') != policy.get('version'): policy_versions.append(pp)
            except: pass
    policy_versions.sort(key=lambda p:p.get('effective_date','1900-01-01'))
    def policy_for(d):
        eligible=[p for p in policy_versions if p.get('effective_date','1900-01-01') <= d.date().isoformat()]
        return eligible[-1] if eligible else policy_versions[0]
    version=policy.get('version','5.0'); rules=policy.get('rules',{})
    prepared=[]
    for raw in rows:
        r=dict(raw); r['_created']=dt(r.get('created_at')) or datetime(2026,11,1); r['_need']=dt(r.get('need_by_date')) or r['_created']
        r['_item']=match_item(r,items); r['_code']=r['_item']['item_code'] if r['_item'] else 'NEW'; r['_qty']=float(r.get('quantity') or 0)
        r['_emergency']='emergency' in str(r.get('urgency','')).lower(); prepared.append(r)
    prepared.sort(key=lambda r:(r['_created'],r.get('pr_no',''),int(r.get('pr_line') or 0)))
    # Greedy earliest eligible requirement; new items are never clubbed.
    groups=[]
    for r in prepared:
        match=None
        if r['_item']:
            for g in groups:
                first=g[0]
                if first['_code']==r['_code'] and first.get('plant')==r.get('plant') and (r['_created']-first['_created']).days<=14 and abs((r['_need']-first['_need']).days)<=7:
                    match=g;break
        if match is None:groups.append([r])
        else:match.append(r)
    supplier_map={s['supplier_id']:s for s in suppliers}; contract_usage={r['contract_id']:float(r.get('consumed_value') or 0) for r in regs}
    # Requirement decisions in chronological order; contract ceilings consumed sequentially.
    results=[]; reqs=[]
    for idx,g in enumerate(groups,1):
        first=g[0]; reqdate=first['_created']; active_policy=policy_for(reqdate); version=active_policy.get('version','5.0'); rules=active_policy.get('rules',{}); code=first['_code']; item=first['_item']; qty=sum(x['_qty'] for x in g); emergency=any(x['_emergency'] for x in g)
        if not item:
            price=float(first.get('estimated_unit_price') or 0); value=qty*price
            req=dict(group_id=f'REQ-{idx:04d}',lines=g,item=None,code='NEW',qty=qty,value=value,price=price,route='NEW_ITEM_SOURCING',decision='SOURCING_REQUEST',supplier='',suggestions=[],clause='1.2',reason='No item-master match; new items are never clubbed and have no supplier suggestions.',contract='')
            reqs.append(req);continue
        hist365=[p for p in pos if p['item_code']==code and dt(p['po_date']) and reqdate-timedelta(days=365)<=dt(p['po_date'])<=reqdate and supplier_map.get(p['supplier_id'],{}).get('status')=='Active']
        applicable=[]
        for c in regs:
            if dt(c['start_date'])<=reqdate<=dt(c['end_date']) and supplier_map.get(c['supplier_id'],{}).get('status')=='Active':
                for l in clines:
                    if l['contract_id']==c['contract_id'] and l['item_code']==code:applicable.append((float(l['rate']),c,l))
        # 3.1 fallback valuation uses lowest applicable rate if every contract lacks ceiling.
        available=[]
        for rate,c,l in applicable:
            cap=float(c.get('value_cap') or 0); used=contract_usage.get(c['contract_id'],0)
            if used+qty*rate<=cap:available.append((rate,c,l))
        chosen=min(available,key=lambda x:x[0]) if available else None
        fallback=min(applicable,key=lambda x:x[0]) if applicable else None
        lastpo=max(hist365,key=lambda p:dt(p['po_date'])) if hist365 else None
        price=chosen[0] if chosen else (fallback[0] if fallback else (float(lastpo['unit_price']) if lastpo else float(item['standard_price'])))
        value=qty*price
        pref=ranking(code,reqdate,items,suppliers,pos,regs,clines)
        suggestions=[]
        # 6.4 top up with active category suppliers, best service record first.
        for x in pref:suggestions.append(x['supplier_id'])
        extras=sorted([s for s in suppliers if s['status']=='Active' and item['category'] in s.get('categories','') and s['supplier_id'] not in suggestions],key=lambda s:(-(0.5*float(s.get('on_time_pct') or 0)/100+0.5*float(s.get('quality_rating') or 0)/5),s['supplier_id']))
        for s in extras:suggestions.append(s['supplier_id'])
        route=''; decision='SOURCING_REQUEST'; sid=''; clause=''; reason=''
        # Extensible policy rules: a Procurement-approved JSON rule can target a plant,
        # category, item type, urgency, value band, contract/history state, etc.
        custom_rule=None
        context={'plant':first.get('plant',''),'category':item.get('category',''),'item_type':item.get('item_type',''),'emergency':emergency,'value':value,'contract_available':bool(chosen),'active_purchase_within_days':bool(hist365),'urgency':first.get('urgency','')}
        for cr in active_policy.get('additional_rules',[]):
            cond=cr.get('when',{}); ok=True
            for k,v in cond.items():
                actual=context.get(k)
                if k in ('value_lt','value_lte','value_gt','value_gte'):
                    num=float(v); ok=ok and ({'value_lt':value<num,'value_lte':value<=num,'value_gt':value>num,'value_gte':value>=num}[k])
                elif k=='active_purchase_within_days': ok=ok and bool(hist365)==bool(v)
                elif k=='contract_available': ok=ok and bool(chosen)==bool(v)
                elif k=='urgency': ok=ok and str(actual).lower()==str(v).lower()
                elif k=='emergency': ok=ok and bool(actual)==bool(v)
                else: ok=ok and str(actual).lower()==str(v).lower()
            if ok: custom_rule=cr; break
        if custom_rule:
            route=custom_rule.get('route','RFQ');decision=custom_rule.get('decision','SOURCING_REQUEST');clause=custom_rule.get('id','CUSTOM');reason=custom_rule.get('description','Additional policy rule matched.')
            count=int(custom_rule.get('suggestions',3));suggestions=suggestions[:count] if decision!='PO' else []
            if custom_rule.get('supplier_source')=='first_preferred' and pref:
                sid=pref[0]['supplier_id'];price=pref[0]['reference_price'];value=qty*price
            elif custom_rule.get('supplier_id'): sid=custom_rule['supplier_id']
            contract=''
        elif item['item_type'].lower()=='capex':route='CAPEX_SOURCING';clause='5.1';reason='Capex items always require sourcing and capex approval.'; suggestions=suggestions[:5]
        elif chosen:
            route='CONTRACT_PO';decision='PO';sid=chosen[1]['supplier_id'];price=chosen[0];value=qty*price;clause='5.2';reason=f"Rate contract {chosen[1]['contract_id']} applies and has sufficient ceiling.";contract=chosen[1]['contract_id'];contract_usage[contract]+=value
        elif emergency and value<=float(rules.get('emergency_limit',500000)) and hist365 and pref:
            route='EMERGENCY_PO';decision='PO';sid=pref[0]['supplier_id'];price=pref[0]['reference_price'];value=qty*price;clause='5.3';reason='Emergency requirement is within emergency limit and item has recent active-supplier purchase history.';contract=''
        elif value<float(rules.get('spot_limit',200000)) and hist365 and pref:
            route='SPOT_PO';decision='PO';sid=pref[0]['supplier_id'];price=pref[0]['reference_price'];value=qty*price;clause='5.4';reason='Below spot limit and item was purchased from an Active supplier in the prior 365 days.';contract=''
        elif value<float(rules.get('rfq_limit',1000000)):
            route='RFQ';clause='5.5';reason='Below ₹10 lakh; no qualifying direct-PO rule applies.';suggestions=suggestions[:3];contract=''
        else:
            route='RFP';clause='5.6';reason='Requirement value is ₹10 lakh or more.';suggestions=suggestions[:5];contract=''
        if route=='CAPEX_SOURCING':contract=''
        reqs.append(dict(group_id=f'REQ-{idx:04d}',lines=g,item=item,code=code,qty=qty,value=value,price=price,route=route,decision=decision,supplier=sid,suggestions=suggestions,clause=clause,reason=reason,contract=contract))
    # Consolidate PO requirements into earliest qualifying PO (same supplier, plant, route, within 24h).
    po_groups=[]; po_counter=1
    for req in reqs:
        if req['decision']!='PO':continue
        t=req['lines'][0]['_created']; plant=req['lines'][0].get('plant'); found=None
        for pg in po_groups:
            if pg['supplier']==req['supplier'] and pg['plant']==plant and pg['route']==req['route'] and (t-pg['first']).total_seconds()<=86400:
                found=pg;break
        if found is None:
            found=dict(po_id=f'PO-{po_counter:04d}',supplier=req['supplier'],plant=plant,route=req['route'],first=t,reqs=[]);po_counter+=1;po_groups.append(found)
        found['reqs'].append(req);req['po_id']=found['po_id']
    out=[]
    for req in reqs:
        for line in req['lines']:
            out.append(dict(pr_no=line.get('pr_no',''),pr_line=line.get('pr_line',''),resolved_item_code=req['code'],group_id=req['group_id'],po_id=req.get('po_id',''),decision=req['decision'],route=req['route'],supplier_id=req['supplier'],unit_price=f"{req['price']:.2f}" if req['decision']=='PO' else '',group_value_inr=f"{req['value']:.2f}",suggested_supplier_ids=';'.join(req['suggestions']) if req['decision']!='PO' else '',policy_version=version,reason=f"Clause {req['clause']} · {req['reason']}"+(f" Relied on {req['contract']}." if req.get('contract') else (f" Recent PO {max([p for p in pos if p['item_code']==req['code'] and p['supplier_id']==req['supplier']],key=lambda p:dt(p['po_date']))['po_number']}." if req['decision']=='PO' and req['supplier'] and any(p['item_code']==req['code'] and p['supplier_id']==req['supplier'] for p in pos) else ''))))
    return out,reqs

OUTPUT_COLUMNS=['pr_no','pr_line','resolved_item_code','group_id','po_id','decision','route','supplier_id','unit_price','group_value_inr','suggested_supplier_ids','policy_version','reason']
