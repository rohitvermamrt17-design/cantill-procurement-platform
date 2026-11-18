from app.engine import DATA, readcsv, process, ranking, load_data
from datetime import datetime

def run():
    rows=readcsv(DATA/'pr_batches/sample_pr_batch.csv')
    out,reqs=process(rows)
    assert len(out)==len(rows)
    bypr={x['pr_no']:x for x in out}
    # Worked-example regression checks.
    assert bypr['PR-26-00019']['route']=='CONTRACT_PO'
    assert bypr['PR-26-00045']['supplier_id']=='S021' # PDF-only RC-2026-031
    assert bypr['PR-26-00004']['resolved_item_code']=='ITM-1022' # "brg" alias and clubbing
    assert bypr['PR-26-00004']['group_id']==bypr['PR-26-00026']['group_id']
    assert bypr['PR-26-00088']['route']=='SPOT_PO'
    assert bypr['PR-26-00059']['route']=='EMERGENCY_PO'
    assert bypr['PR-26-00089']['route']=='RFQ'
    assert bypr['PR-26-00100']['route']=='RFP'
    assert bypr['PR-26-00047']['route']=='CAPEX_SOURCING'
    assert bypr['PR-26-00170']['route']=='NEW_ITEM_SOURCING'
    # Never suggest blocked or inactive suppliers.
    _,sups,_,_,_,_=load_data(); active={s['supplier_id'] for s in sups if s['status']=='Active'}
    for row in out:
        assert set(filter(None,row['suggested_supplier_ids'].split(';'))).issubset(active)
    # Supplier example rankings.
    items,sups,pos,regs,lines,_=load_data(); rank=ranking('ITM-1048',datetime(2026,11,1),items,sups,pos,regs,lines)
    assert rank[0]['supplier_id']=='S029'
    print(f'PASS: {len(rows)} lines, {len(reqs)} requirements, {sum(r["decision"]=="PO" for r in reqs)} POs, {sum(r["decision"]!="PO" for r in reqs)} sourcing requirements')
if __name__=='__main__':run()
