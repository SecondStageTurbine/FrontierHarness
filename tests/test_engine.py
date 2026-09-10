"""Deterministic test doubles live only in tests; production always calls a provider."""
import asyncio
import json
import pytest
from backend.store import Store, TenantIsolationViolationException, uid, now
from backend.schemas import TenantInput, ModelConfig, WorkflowInput, PlanArtifact, ReviewReport
from backend.engine import Engine
from backend.broker import ModelResult, ProviderError

PLAN = {'title':'Implementation plan','execution_steps':['Implement tenant isolation','Add regression tests'],'technical_dependencies':[{'name':'FastAPI'}],'risk_assessment':'Verify every tenant boundary.'}
BUILD = {'summary':'Implementation prepared. The harness will run the project checks.','files':[{'name':'result.py','content':'def answer():\n    return 42\n'},{'name':'test_result.py','content':'import unittest\nfrom result import answer\n\nclass ResultTest(unittest.TestCase):\n    def test_answer(self):\n        self.assertEqual(answer(), 42)\n'}],'commands':['python -m unittest discover']}
APPROVED = {'approved':True,'quality_score':0.95,'detected_vulnerabilities':[],'rejection_reasons':[]}
REJECTED = {'approved':False,'quality_score':0.5,'detected_vulnerabilities':['Missing ownership check'],'rejection_reasons':['Add explicit tenant ownership validation.']}

class ScriptedBroker:
    def __init__(self, reviews=None, malformed=None, delay=0):
        self.reviews = reviews or [APPROVED]
        self.malformed = malformed
        self.delay = delay
        self.calls = []
        self.review_index = 0

    async def invoke(self, tenant_id, model_id, system, prompt, stage, schema=None):
        self.calls.append({'tenant_id':tenant_id,'model_id':model_id,'role':stage['role'],'prompt':prompt,'system':system})
        await asyncio.sleep(self.delay)
        role = stage['role']
        if self.malformed == role:
            text = '{"invalid":true}'
        elif role == 'review':
            text = json.dumps(self.reviews[min(self.review_index,len(self.reviews)-1)])
            self.review_index += 1
        else:
            text = json.dumps(PLAN if role=='planning' else BUILD) if role!='discussion' else 'Discussion: build safely and verify assumptions.'
        return ModelResult(text,100,50)

def setup_store(tmp_path):
    store = Store(str(tmp_path))
    for t in ['tenant-a','tenant-b']:
        value = {'id':t,**TenantInput(name=t,max_workflow_iterations=5).model_dump()}
        with store.db() as db:
            db.execute('INSERT INTO tenants VALUES(?,?,?)',(t,'owner-'+t,json.dumps(value)))
    for t in ['tenant-a','tenant-b']:
        for role,provider in [('discussion','anthropic'),('planning','anthropic'),('building','openai'),('review','custom_openai')]:
            store.put(t,'models',{'id':f'{provider}/{role}','name':role,'provider':provider,'model_name':role,'input_price':1.0,'output_price':2.0,'encrypted_key':store.encrypt('private-'+t),'key_hint':'••••test'})
        workflow = WorkflowInput(name='A real backend workflow',objective='Build a secure multi-tenant API with tests.',max_workflow_iterations=3,stages=[{'id':role,'role':role,'model_id':f'{provider}/{role}'} for role,provider in [('discussion','anthropic'),('planning','anthropic'),('building','openai'),('review','custom_openai')]])
        store.put(t,'workflows',{'id':'workflow-'+t,**workflow.model_dump(),'created_at':now()})
    return store

async def finish(engine,tenant='tenant-a',workflow='workflow-tenant-a'):
    run = engine.start(tenant,workflow)
    await engine.tasks[(tenant,run['id'])]
    return engine.store.get(tenant,'runs',run['id'])

@pytest.mark.asyncio
async def test_full_pipeline_and_cross_provider_routing(tmp_path):
    store=setup_store(tmp_path);broker=ScriptedBroker();engine=Engine(store,broker)
    run=await finish(engine)
    assert run['status']=='complete'
    assert [c['role'] for c in broker.calls]==['discussion','planning','building','review']
    assert [c['model_id'].split('/')[0] for c in broker.calls]==['anthropic','anthropic','openai','custom_openai']
    assert run['iteration']==1 and len(run['stages'])==4
    assert run['cost']==pytest.approx(0.0008)
    assert run['input_tokens']==400 and run['output_tokens']==200
    assert 'Discussion: build safely' in broker.calls[1]['prompt']
    assert 'result.py' in broker.calls[-1]['prompt']

@pytest.mark.asyncio
async def test_rejected_review_replans_with_complete_feedback(tmp_path):
    store=setup_store(tmp_path);broker=ScriptedBroker([REJECTED,APPROVED]);engine=Engine(store,broker)
    run=await finish(engine)
    assert run['status']=='complete' and run['iteration']==2
    assert len(broker.calls)==7
    assert 'Missing ownership check' in broker.calls[4]['prompt']
    assert 'Add explicit tenant ownership validation' in broker.calls[4]['prompt']
    assert [s['artifact']['approved'] for s in run['stages'] if s['role']=='review']==[False,True]
    assert any(e['type']=='workflow.replanning' for e in store.events('tenant-a',run['id']))

@pytest.mark.asyncio
async def test_iteration_circuit_breaker_and_continue(tmp_path):
    store=setup_store(tmp_path);broker=ScriptedBroker([REJECTED]);engine=Engine(store,broker)
    run=await finish(engine)
    assert run['status']=='limit_reached' and run['iteration']==3 and len(broker.calls)==10
    assert run['error_code']=='WorkflowIterationLimitExceeded'
    with pytest.raises(ValueError):engine.continue_run('tenant-a',run['id'],6)
    broker.reviews=[APPROVED]
    engine.continue_run('tenant-a',run['id'],4)
    await engine.tasks[('tenant-a',run['id'])]
    run=store.get('tenant-a','runs',run['id'])
    assert run['status']=='complete' and run['iteration']==4 and len(broker.calls)==13
    assert len([s for s in run['stages'] if s['role']=='review'])==4

@pytest.mark.parametrize('role',['planning','building','review'])
@pytest.mark.asyncio
async def test_invalid_artifacts_fail_closed_and_preserve_raw_output(tmp_path,role):
    store=setup_store(tmp_path);broker=ScriptedBroker(malformed=role);engine=Engine(store,broker)
    run=await finish(engine)
    assert run['status']=='failed' and run['error_code']=='ArtifactValidationError'
    assert run['stages'][-1]['output']=='{"invalid":true}'
    assert run['stages'][-1]['status']=='failed'

@pytest.mark.asyncio
async def test_quality_threshold_forces_replan(tmp_path):
    store=setup_store(tmp_path);broker=ScriptedBroker([{**APPROVED,'quality_score':0.1},APPROVED]);engine=Engine(store,broker)
    run=await finish(engine)
    assert run['iteration']==2
    assert 'below the workflow' in run['stages'][3]['artifact']['rejection_reasons'][0]

def test_tenant_isolation_applies_to_internal_access(tmp_path):
    store=setup_store(tmp_path)
    with pytest.raises(TenantIsolationViolationException):store.get('tenant-a','workflows','workflow-tenant-b')
    with pytest.raises(TenantIsolationViolationException):store.get('','models','anthropic/planning')
    with pytest.raises(TenantIsolationViolationException):store.tenant('tenant-a','owner-tenant-b')
    with pytest.raises(TenantIsolationViolationException):store.put('tenant-a','models',{'id':'bad','tenant_id':'tenant-b'})
    assert store.decrypt(store.get('tenant-a','models','anthropic/planning')['encrypted_key'])=='private-tenant-a'
    assert store.decrypt(store.get('tenant-b','models','anthropic/planning')['encrypted_key'])=='private-tenant-b'

@pytest.mark.asyncio
async def test_workflow_cannot_reference_foreign_resource(tmp_path):
    store=setup_store(tmp_path);engine=Engine(store,ScriptedBroker())
    store.put('tenant-b','models',{'id':'foreign-only','name':'foreign'})
    w=store.get('tenant-a','workflows','workflow-tenant-a');w['stages'][1]['model_id']='foreign-only'
    with pytest.raises(TenantIsolationViolationException):engine.validate_workflow('tenant-a',w)

@pytest.mark.asyncio
async def test_budget_stops_before_model_call(tmp_path):
    store=setup_store(tmp_path);broker=ScriptedBroker();engine=Engine(store,broker)
    t=store.tenant_internal('tenant-a');t['max_cost_per_run']=0.00001
    with store.db() as db:db.execute('UPDATE tenants SET data=? WHERE id=?',(json.dumps(t),'tenant-a'))
    run=await finish(engine)
    assert run['status']=='failed' and run['error_code']=='ExecutionLimitError' and not broker.calls

@pytest.mark.asyncio
async def test_unknown_pricing_not_reported_as_zero(tmp_path):
    store=setup_store(tmp_path);broker=ScriptedBroker();engine=Engine(store,broker)
    m=store.get('tenant-a','models','anthropic/planning');m['input_price']=None;store.put('tenant-a','models',m)
    run=await finish(engine)
    assert run['status']=='complete' and run['cost_complete'] is False
    assert run['stages'][1]['cost'] is None

@pytest.mark.asyncio
async def test_cancel_and_single_active_tenant_run(tmp_path):
    store=setup_store(tmp_path);broker=ScriptedBroker(delay=10);engine=Engine(store,broker)
    run=engine.start('tenant-a','workflow-tenant-a');await asyncio.sleep(0.01)
    with pytest.raises(ValueError):engine.start('tenant-a','workflow-tenant-a')
    result=await engine.cancel('tenant-a',run['id'])
    assert result['status']=='cancelled' and len(broker.calls)==1
    # The interrupted call is billed at the bound the budget already reserved, so the
    # workspace is never left holding an unreconcilable charge. Token counts stay partial.
    assert result['cost_complete'] is True and result['cost']>0 and result['usage_complete'] is False

@pytest.mark.asyncio
async def test_stopped_run_does_not_block_the_monthly_budget(tmp_path):
    store=setup_store(tmp_path);engine=Engine(store,ScriptedBroker(delay=10))
    t=store.tenant_internal('tenant-a');t['monthly_budget']=5.0
    with store.db() as db:db.execute('UPDATE tenants SET data=? WHERE id=?',(json.dumps(t),'tenant-a'))
    stopped=engine.start('tenant-a','workflow-tenant-a');await asyncio.sleep(0.01)
    await engine.cancel('tenant-a',stopped['id'])
    engine.broker=ScriptedBroker()
    assert (await finish(engine))['status']=='complete'

@pytest.mark.asyncio
async def test_persistent_runs_survive_store_reopen(tmp_path):
    store=setup_store(tmp_path);engine=Engine(store,ScriptedBroker());run=await finish(engine)
    reopened=Store(str(tmp_path))
    assert reopened.get('tenant-a','runs',run['id'])['stages']==run['stages']
    assert reopened.events('tenant-a',run['id'])
    with pytest.raises(TenantIsolationViolationException):reopened.events('tenant-b',run['id'])

@pytest.mark.asyncio
async def test_restart_marks_active_run_without_replay(tmp_path):
    store=setup_store(tmp_path);broker=ScriptedBroker();engine=Engine(store,broker);run=await finish(engine)
    run.update(status='running',phase='BUILDING');store.put('tenant-a','runs',run)
    engine.recover()
    assert store.get('tenant-a','runs',run['id'])['status']=='interrupted'
    assert len(broker.calls)==4

def test_schema_rejects_unsafe_pipeline_and_inconsistent_review(tmp_path):
    store=setup_store(tmp_path);w=store.get('tenant-a','workflows','workflow-tenant-a')
    w['stages'].reverse()
    with pytest.raises(ValueError):Engine(store).validate_workflow('tenant-a',w)
    with pytest.raises(ValueError):ReviewReport.model_validate({**APPROVED,'approved':'true'})
    with pytest.raises(ValueError):ReviewReport.model_validate({**APPROVED,'detected_vulnerabilities':['Bad']})
    with pytest.raises(ValueError):ReviewReport.model_validate({**APPROVED,'quality_score':2})

@pytest.mark.asyncio
async def test_bounded_transient_fallback(tmp_path):
    store=setup_store(tmp_path)
    class FallbackBroker(ScriptedBroker):
        async def invoke(self,tenant_id,model_id,system,prompt,stage,schema=None):
            if model_id=='anthropic/planning':
                raise ProviderError('Temporary outage',True)
            return await super().invoke(tenant_id,model_id,system,prompt,stage,schema)
    w=store.get('tenant-a','workflows','workflow-tenant-a');w['stages'][1]['fallback_model_id']='openai/building';store.put('tenant-a','workflows',w)
    t=store.tenant_internal('tenant-a');t['max_cost_per_run']=1.0
    with store.db() as db:db.execute('UPDATE tenants SET data=? WHERE id=?',(json.dumps(t),'tenant-a'))
    run=await finish(Engine(store,FallbackBroker()))
    assert run['status']=='complete' and run['stages'][1]['model_id']=='openai/building'
    # With a budget set, the abandoned attempt is charged at its bound instead of making
    # the cost unknown, which would have rejected the fallback's own budget check.
    assert run['cost_complete'] and run['stages'][1]['cost']>0
