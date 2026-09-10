from types import SimpleNamespace
import pytest
from backend import broker
from backend.schemas import BuildArtifact, PlanArtifact
from tests.test_engine import setup_store, BUILD, PLAN

@pytest.mark.asyncio
async def test_anthropic_enforces_closed_contract_without_losing_dependency_maps(tmp_path,monkeypatch):
    requests=[];credentials=[]
    class Client:
        def __init__(self,**options):
            credentials.append(options['api_key']);self.messages=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def create(self,**options):
            requests.append(options)
            data=BUILD if options['tools'][0]['input_schema']['title']=='BuildArtifact' else PLAN
            return SimpleNamespace(content=[SimpleNamespace(type='tool_use',name='submit_artifact',input=data)],usage=SimpleNamespace(input_tokens=100,output_tokens=50),stop_reason='tool_use')
    monkeypatch.setattr(broker,'AsyncAnthropic',Client)
    store=setup_store(tmp_path)
    service=broker.ModelBroker(store)
    stage={'max_tokens':1000,'timeout':10}
    result=await service.invoke('tenant-a','anthropic/planning','Build files.','Objective',stage,BuildArtifact.model_json_schema())
    assert BuildArtifact.model_validate_json(result.text).files
    tool=requests[-1]['tools'][0]
    assert tool['strict'] is True and tool['input_schema']['properties']['files']['type']=='array'
    assert 'maxItems' not in tool['input_schema']['properties']['files']
    assert BuildArtifact.model_json_schema()['properties']['files']['maxItems']==50
    await service.invoke('tenant-b','anthropic/planning','Plan.','Objective',stage,PlanArtifact.model_json_schema())
    tool=requests[-1]['tools'][0]
    assert 'strict' not in tool
    assert tool['input_schema']['properties']['technical_dependencies']['items']['additionalProperties']=={'type':'string'}
    assert credentials==['private-tenant-a','private-tenant-b']
