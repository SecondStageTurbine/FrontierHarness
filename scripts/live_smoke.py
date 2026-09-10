"""Opt-in real-provider integration test against the same engine used by the UI.

Uses an isolated ephemeral tenant/database and an explicit tenant credential. No
environment-key fallback exists inside the engine. Only a redacted report is saved.
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from backend.store import Store, now
from backend.schemas import TenantInput, WorkflowInput
from backend.engine import Engine

async def main():
    model_name=os.environ.get('HARNESS_SMOKE_MODEL','claude-haiku-4-5-20251001')
    with tempfile.TemporaryDirectory(prefix='frontier-live-') as directory:
        store=Store(directory)
        tenant={'id':'live-smoke',**TenantInput(name='Isolated live verification',max_workflow_iterations=2).model_dump()}
        with store.db() as db:
            db.execute('INSERT INTO tenants VALUES(?,?,?)',(tenant['id'],'smoke-test',json.dumps(tenant)))
        model_id='anthropic/'+model_name
        store.put(tenant['id'],'models',{'id':model_id,'name':'Live Anthropic verification','model_name':model_name,'provider':'anthropic','base_url':None,'input_price':None,'output_price':None,'encrypted_key':store.encrypt(os.environ['ANTHROPIC_API_KEY']),'key_hint':'configured'})
        w=WorkflowInput(name='Live provider smoke test',objective='Create a tiny Python module containing add(a, b) that returns the sum of two integers, and a unittest test file covering positive and negative integers. Include a short README explaining how to run the tests. Do not run any code. Deliver only these three small files.',context='This is a bounded integration test. Keep every response concise. Standard library only. Generating a deliverable is sufficient; executing the tests is explicitly out of scope.',max_workflow_iterations=2,minimum_quality=0.7,stages=[{'id':r,'role':r,'model_id':model_id,'max_tokens':1800,'timeout':90} for r in ['discussion','planning','building','review']])
        store.put(tenant['id'],'workflows',{'id':'live-workflow',**w.model_dump(),'created_at':now()})
        engine=Engine(store)
        run=engine.start(tenant['id'],'live-workflow')
        await engine.tasks[(tenant['id'],run['id'])]
        result=store.get(tenant['id'],'runs',run['id'])
        report={k:result[k] for k in ['status','iteration','input_tokens','output_tokens','error','error_code','started_at','finished_at']}
        report['model']=model_name
        report['stages']=[{'role':s['role'],'status':s['status'],'iteration':s['iteration'],'artifact':s['artifact'],'output':s['output']} for s in result['stages']]
        report['validation_errors']=result.get('validation_errors',[])
        Path('test-results').mkdir(exist_ok=True)
        Path('test-results/live-provider.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='stages'},indent=2))
        if result['status']!='complete':
            raise SystemExit(1)

if __name__=='__main__':
    asyncio.run(main())
