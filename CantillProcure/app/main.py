import sys,csv,io,json,re,sqlite3,uuid,subprocess,tempfile
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
sys.path.insert(0,str(Path(__file__).parent))
from engine import *
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'cantill.db'; POLICY=ROOT/'data'/'policy_v5.json'
app=FastAPI(title='Cantill Procure',version='1.0')
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
def db():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def init():
 c=db();c.execute('''CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, target TEXT, detail TEXT, created_at TEXT)''');c.execute('''CREATE TABLE IF NOT EXISTS pending_policy(id TEXT PRIMARY KEY, policy_json TEXT, summary TEXT, effective_date TEXT, created_at TEXT)''');c.commit();c.close()
init()
def audit(actor,action,target,detail):
 c=db();c.execute('INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)',(actor,action,target,detail,datetime.now().isoformat(timespec='seconds')));c.commit();c.close()
def policy():return json.loads(POLICY.read_text())
def rows_from_bytes(b):
 try:return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))
 except Exception as e:raise HTTPException(400,f'Invalid CSV: {e}')
def csv_response(rows,filename):
 s=io.StringIO();w=csv.DictWriter(s,fieldnames=OUTPUT_COLUMNS);w.writeheader();w.writerows(rows)
 return StreamingResponse(iter([s.getvalue()]),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="{filename}"'})
@app.get('/',response_class=HTMLResponse)
def home():return (ROOT/'static'/'index.html').read_text()
@app.get('/api/summary')
def summary():
 items,sups,pos,regs,lines,plants=load_data();p=policy();return {'items':len(items),'suppliers':len(sups),'purchase_orders':len(pos),'contracts':len(regs),'plants':plants,'policy_version':p['version'],'effective_date':p['effective_date']}
@app.post('/api/process-batch')
async def process_batch(file:UploadFile=File(...)):
 global LATEST
 rows=rows_from_bytes(await file.read());out,reqs=process(rows);LATEST=out;audit('Procurement','PROCESS_BATCH',file.filename,f'{len(rows)} PR lines, {len(reqs)} requirements')
 return {'count':len(out),'requirements':len(reqs),'decisions':out,'summary':{'po':sum(r['decision']=='PO' for r in reqs),'sourcing':sum(r['decision']!='PO' for r in reqs)},'download_url':'/api/export/latest'}
LATEST=[]
@app.post('/api/process-batch/export')
async def process_export(file:UploadFile=File(...)):
 global LATEST
 rows=rows_from_bytes(await file.read());LATEST,_=process(rows);audit('Procurement','EXPORT_DECISIONS',file.filename,f'{len(LATEST)} lines');return csv_response(LATEST,'procurement_decisions.csv')
@app.get('/api/export/latest')
def export_latest():
 if not LATEST:raise HTTPException(404,'Process a batch first')
 return csv_response(LATEST,'procurement_decisions.csv')
@app.get('/api/preferred-suppliers')
def preferred(as_of:str='2026-11-01'):
 items,sups,pos,regs,lines,plants=load_data();d=dt(as_of) or datetime(2026,11,1);out=[]
 for item in items:
  ranked=ranking(item['item_code'],d,items,sups,pos,regs,lines)
  for n,s in enumerate(ranked,1):out.append({'item_code':item['item_code'],'rank':n,'supplier_id':s['supplier_id'],'reference_price':f"{s['reference_price']:.2f}",'supplier_name':s['name'],'score':round(s['score'],4),'po_count_548d':s['po_count'],'recency_score':s['recency'],'contract_held':s['contract'],'on_time_pct':round(s['on_time']*100,1),'quality_rating':round(s['quality']*5,2),'evidence':s['last_po'] or 'contract rate'})
 return {'as_of':as_of,'rows':out}
@app.get('/api/preferred-suppliers/export')
def preferred_export(as_of:str='2026-11-01'):
 data=preferred(as_of)['rows'];s=io.StringIO();w=csv.DictWriter(s,fieldnames=['item_code','rank','supplier_id','reference_price']);w.writeheader();w.writerows([{k:r[k] for k in w.fieldnames} for r in data]);return StreamingResponse(iter([s.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="preferred_suppliers.csv"'})
@app.get('/api/contracts')
def contracts():
 _,sups,_,regs,lines,_=load_data();return {'contracts':[{**r,'items':[l for l in lines if l['contract_id']==r['contract_id']]} for r in regs]}
@app.post('/api/contracts')
async def add_contract(file:UploadFile=File(...),contract_id:str=Form(...),supplier_id:str=Form(...),start_date:str=Form(...),end_date:str=Form(...),value_cap:float=Form(...)):
 if not file.filename.lower().endswith('.pdf'):raise HTTPException(400,'Upload a signed PDF')
 content=await file.read();dest=ROOT/'data'/'contracts'/'pdf'/f'{contract_id}.pdf';dest.write_bytes(content)
 # extraction via pdftotext; buyer confirms extracted schedule rows separately in UI.
 txt=subprocess.run(['pdftotext','-layout',str(dest),'-'],capture_output=True,text=True).stdout
 found=re.findall(r'(ITM-\d+)\s+(.+?)\s+(NOS|KG|LTR|MTR|PAIR|BRL|BKT|MON|JOB)\s+([\d,]+\.\d{2})',txt)
 if not found:raise HTTPException(400,'Could not extract item schedule. Check PDF format.')
 _,sups,_,regs,lines,_=load_data();sm=next((s for s in sups if s['supplier_id']==supplier_id),None)
 if not sm:raise HTTPException(400,'Unknown supplier ID')
 regs.append(dict(contract_id=contract_id,supplier_id=supplier_id,supplier_name=sm['name'],start_date=start_date,end_date=end_date,value_cap=str(value_cap),consumed_value='0'))
 lines.extend([dict(contract_id=contract_id,item_code=x[0],description=x[1].strip(),uom=x[2],rate=x[3].replace(',','')) for x in found])
 # Persist supplemental PDF-derived contracts and lines as CSV append.
 with open(ROOT/'data'/'contracts'/'contract_register.csv','a',newline='') as f:csv.DictWriter(f,fieldnames=['contract_id','supplier_id','supplier_name','start_date','end_date','value_cap','consumed_value']).writerow(regs[-1])
 with open(ROOT/'data'/'contracts'/'contract_lines.csv','a',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['contract_id','item_code','description','uom','rate']);w.writerows(lines[-len(found):])
 audit('Buyer','ADD_CONTRACT',contract_id,f'{len(found)} schedule lines extracted from signed PDF');return {'contract_id':contract_id,'extracted_lines':len(found),'items':[x[0] for x in found]}
@app.post('/api/policy/preview')
async def policy_preview(file:UploadFile=File(None),change:str=Form(''),effective_date:str=Form('')):
 current=policy();new=json.loads(json.dumps(current));text=change
 if file:
  b=await file.read();name=file.filename.lower()
  if name.endswith('.json'):
   try:new=json.loads(b.decode('utf-8-sig'));text='Uploaded structured policy JSON'
   except:raise HTTPException(400,'Invalid policy JSON')
  else:
   if name.endswith('.pdf'):
    with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
     f.write(b);f.flush();text=subprocess.run(['pdftotext','-layout',f.name,'-'],capture_output=True,text=True).stdout
   else:text=b.decode('utf-8-sig')
 if not text.strip():raise HTTPException(400,'Upload a policy document or describe the amendment')
 # Extract common threshold changes; keep policy represented as editable structured data.
 changes=[]
 patterns=[('emergency_limit',r'emergency.{0,80}?(?:₹|INR|rs\.?\s*)?([\d,]+)') ,('spot_limit',r'(?:spot|direct\s+po).{0,80}?(?:₹|INR|rs\.?\s*)?([\d,]+)'),('rfq_limit',r'(?:rfp|rfq|sourcing).{0,100}?(?:₹|INR|rs\.?\s*)?([\d,]+)')]
 for key,pat in patterns:
  m=re.search(pat,text,re.I|re.S)
  if m:
   val=float(m.group(1).replace(',',''));old=new['rules'].get(key)
   if old!=val:new['rules'][key]=val;changes.append({'field':key,'from':old,'to':val})
 eff=effective_date or (re.search(r'(20\d{2}-\d{2}-\d{2})',text).group(1) if re.search(r'(20\d{2}-\d{2}-\d{2})',text) else datetime.now().date().isoformat())
 new['version']=str(round(float(current['version'])+0.1,1));new['effective_date']=eff;new.setdefault('change_log',[]).append({'version':new['version'],'effective_date':eff,'source':file.filename if file else 'chat amendment','changes':changes,'raw_text':text[:3000]})
 summary='; '.join([f"{c['field']}: ₹{c['from']:,.0f} → ₹{c['to']:,.0f}" for c in changes]) or 'No known numeric threshold was detected. The amendment text is retained for buyer review; unsupported clauses require structured policy editing before activation.'
 pid=str(uuid.uuid4());c=db();c.execute('INSERT INTO pending_policy VALUES(?,?,?,?,?)',(pid,json.dumps(new),summary,eff,datetime.now().isoformat()));c.commit();c.close();audit('Procurement','POLICY_PREVIEW',pid,summary)
 return {'id':pid,'current_version':current['version'],'proposed_version':new['version'],'effective_date':eff,'summary':summary,'changes':changes,'source_excerpt':text[:1200]}
@app.post('/api/policy/approve/{pid}')
def approve_policy(pid:str,actor:str='Procurement Admin'):
 c=db();r=c.execute('SELECT * FROM pending_policy WHERE id=?',(pid,)).fetchone();c.close()
 if not r:raise HTTPException(404,'Pending policy not found')
 new=json.loads(r['policy_json']); hist=ROOT/'data'/'policies';hist.mkdir(exist_ok=True); old=policy(); (hist/f"policy_{old['version']}.json").write_text(json.dumps(old,indent=2)); (hist/f"policy_{new['version']}.json").write_text(json.dumps(new,indent=2)); POLICY.write_text(json.dumps(new,indent=2));audit(actor,'APPROVE_POLICY',pid,f"Activated v{new['version']} effective {new['effective_date']}");return {'active_version':new['version'],'effective_date':new['effective_date']}
@app.get('/api/policy')
def get_policy():return policy()
@app.post('/api/chat')
def chat(payload:dict):
 text=payload.get('message','').strip();items,*_=load_data()
 # Prefer an explicit item-master code when the employee includes one in chat.
 code_match=re.search(r'\b(ITM-\d+)\b',text,re.I)
 item=match_item({'item_code':code_match.group(1) if code_match else '', 'description':text},items)
 # lightweight guided conversational intake; never create until required details are confirmed.
 qty=re.search(r'\b(\d+(?:\.\d+)?)\b',text);plant=next((p for p in ['PUN1','CHK1'] if p in text.upper()),'');need=re.search(r'(20\d{2}-\d{2}-\d{2})',text)
 missing=[]
 if not item:missing.append('item')
 if not qty:missing.append('quantity')
 if not plant:missing.append('plant (PUN1 or CHK1)')
 if not need:missing.append('need-by date (YYYY-MM-DD)')
 if missing:return {'status':'needs_information','message':('I could not confidently match that item. Please provide its exact item-master description or code. ' if not item else '')+'Please provide '+', '.join(missing)+'.','matched_item':item,'missing':missing}
 return {'status':'ready_to_confirm','message':f"Please confirm: {qty.group(1)} {item['uom']} of {item['description']} ({item['item_code']}) for {plant}, needed by {need.group(1)}. No PR has been created yet.",'draft':{'item_code':item['item_code'],'description':item['description'],'quantity':float(qty.group(1)),'uom':item['uom'],'plant':plant,'need_by_date':need.group(1)}}
@app.post('/api/action')
def buyer_action(payload:dict):
 action=payload.get('action');reason=payload.get('reason','').strip();target=payload.get('target','')
 if action=='override' and not reason:raise HTTPException(400,'A written override reason is mandatory')
 audit(payload.get('actor','Buyer'),action,target,reason or 'Buyer approved original recommendation');return {'status':'recorded','action':action,'target':target}
@app.get('/api/audit')
def get_audit():
 c=db();rows=[dict(x) for x in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')];c.close();return rows
@app.post('/api/chat/confirm')
def chat_confirm(payload:dict):
 d=payload.get('draft',{});required=['item_code','quantity','plant','need_by_date']
 if any(not d.get(k) for k in required):raise HTTPException(400,'Incomplete PR draft')
 pr='CHAT-'+datetime.now().strftime('%y%m%d%H%M%S');audit(payload.get('requester','Employee'),'CREATE_PR',pr,json.dumps(d));return {'status':'created','pr_no':pr,'message':f'Purchase requisition {pr} created and queued for Procurement.'}
