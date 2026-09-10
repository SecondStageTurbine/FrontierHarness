"""Transport-independent contracts. The engine validates all generated artifacts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from urllib.parse import urlparse

Role = Literal['discussion', 'planning', 'building', 'review']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class PlanArtifact(StrictModel):
    title: str = Field(min_length=1)
    execution_steps: list[str] = Field(min_length=1)
    technical_dependencies: list[dict[str, str]]
    risk_assessment: str = Field(min_length=1)

class ReviewReport(StrictModel):
    approved: bool = Field(strict=True)
    quality_score: float = Field(ge=0, le=1)
    detected_vulnerabilities: list[str]
    rejection_reasons: list[str]

    @model_validator(mode='after')
    def consistent(self):
        if self.approved and (self.detected_vulnerabilities or self.rejection_reasons):
            raise ValueError('Approval cannot contain unresolved vulnerabilities or rejection reasons.')
        if not self.approved and not (self.rejection_reasons or self.detected_vulnerabilities):
            raise ValueError('Rejected reviews must explain why.')
        return self

class ArtifactFile(StrictModel):
    # Source text must survive validation byte-for-byte, including indentation.
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    name: str = Field(min_length=1, max_length=200, pattern=r'^[\w. /-]+$')
    content: str = Field(max_length=200000)

class BuildArtifact(StrictModel):
    summary: str = Field(min_length=1)
    files: list[ArtifactFile] = Field(max_length=50)
    commands: list[str] = Field(default_factory=list, max_length=6)

class ModelConfig(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    provider: Literal['anthropic', 'openai', 'custom_openai', 'ollama']
    model_name: str = Field(min_length=1, max_length=150)
    api_key: str | None = Field(default=None, max_length=1000)
    base_url: str | None = None
    input_price: float | None = Field(default=None, ge=0)
    output_price: float | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def endpoint(self):
        if self.provider in ('custom_openai', 'ollama') and not self.base_url:
            raise ValueError('A base URL is required for this provider.')
        if self.base_url:
            u = urlparse(self.base_url)
            if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError('Use an HTTP(S) endpoint without credentials, query, or fragment.')
            if self.provider in ('openai', 'anthropic'):
                raise ValueError('Use OpenAI Compatible for a custom endpoint.')
        return self

class Stage(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    role: Role
    model_id: str = Field(min_length=1)
    prompt: str = Field(default='', max_length=20000)
    temperature: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int = Field(default=4096, ge=128, le=32000)
    timeout: int = Field(default=120, ge=10, le=600)
    fallback_model_id: str | None = None
    agent_id: str | None = None

class WorkflowInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=10, max_length=40000)
    context: str = Field(default='', max_length=60000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    stages: list[Stage] = Field(min_length=4, max_length=12)
    max_workflow_iterations: int = Field(default=3, ge=1, le=20)
    minimum_quality: float = Field(default=0.8, ge=0, le=1)
    approval_rules: str = Field(default='No unresolved security vulnerabilities. Satisfy the objective and all constraints.', max_length=20000)
    archived: bool = False
    project_id: str | None = None
    session_id: str | None = None
    execution_mode: Literal['propose', 'edit', 'execute'] = 'propose'

    @model_validator(mode='after')
    def pipeline(self):
        roles = [s.role for s in self.stages]
        rank = {'discussion': 0, 'planning': 1, 'building': 2, 'review': 3}
        if set(roles) != set(rank) or roles != sorted(roles, key=rank.get):
            raise ValueError('Stages must follow Discussion → Planning → Building → Review.')
        if roles.count('planning') != 1 or roles.count('review') != 1:
            raise ValueError('Exactly one planner and one final reviewer are required.')
        if len({s.id for s in self.stages}) != len(self.stages):
            raise ValueError('Stage identifiers must be unique.')
        return self

class TenantInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    environment: str = Field(default='Development', max_length=50)
    max_workflow_iterations: int = Field(default=3, ge=1, le=20)
    max_cost_per_run: float | None = Field(default=None, gt=0)
    monthly_budget: float | None = Field(default=None, gt=0)
    model_routing_table: dict[Role, str] = Field(default_factory=dict)
    corporate_rules: str = Field(default='Protect tenant isolation and credentials. Do not claim unperformed tests or actions.', max_length=20000)

class TenantContext(TenantInput):
    tenant_id: str

class AgentInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    role: Role = 'planning'
    system_prompt: str = Field(min_length=1, max_length=20000)
    model_id: str
    context_rules: str = Field(default='', max_length=10000)

class PromptInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    role: Role = 'planning'
    content: str = Field(min_length=1, max_length=20000)

class LoginInput(StrictModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=200)

class ContinueInput(StrictModel):
    max_workflow_iterations: int = Field(ge=2, le=20)

class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    root: str | None = Field(default=None, max_length=1000)

class SessionInput(StrictModel):
    name: str = Field(default='New session',min_length=1,max_length=120)

class InstructionInput(StrictModel):
    content: str = Field(min_length=2,max_length=40000)
    context: str = Field(default='',max_length=40000)
    team: dict[Role,str]
    agent_ids: dict[Role,str] = Field(default_factory=dict)
    prompt_ids: dict[Role,str] = Field(default_factory=dict)
    execution_mode: Literal['propose','edit','execute'] = 'execute'
    attachment_ids: list[str] = Field(default_factory=list,max_length=10)
    max_workflow_iterations: int = Field(default=3,ge=1,le=20)

class CommandInput(StrictModel):
    command: str = Field(min_length=1,max_length=300)
