"""Transport-independent contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from urllib.parse import urlparse

Mode = Literal['read', 'edit', 'auto']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

# A subscription login runs a local agent command line tool. It is the only kind of model that
# can take a turn, because an API key reaches a model and not an agent: no tools, no file
# access, no shell. Keyed providers stay configurable and are used for dictation.
SUBSCRIPTION_PROVIDERS = ('claude_cli', 'codex_cli', 'opencode_cli')

class ModelConfig(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    provider: Literal['anthropic', 'openai', 'custom_openai', 'ollama', 'claude_cli', 'codex_cli', 'opencode_cli', 'typesafe']
    model_name: str = Field(min_length=1, max_length=150)
    api_key: str | None = Field(default=None, max_length=1000)
    base_url: str | None = None
    input_price: float | None = Field(default=None, ge=0)
    output_price: float | None = Field(default=None, ge=0)
    # Which signed-in subscription serves this agent. The name is a directory segment holding
    # that login's own credentials, so two subscriptions for the same provider can be connected
    # at once and a turn can move between them. Empty keeps the machine's own default sign-in.
    account: str | None = Field(default=None, max_length=40, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    # What this agent is good at, 0-10 per capability, for Adaptive routing. Empty means the
    # default profile for its provider and model family. This is the capability registry: a new
    # agent is a row plus, at most, these numbers, and the router never names a provider.
    capabilities: dict[str, int] | None = None
    cost_class: Literal['free', 'low', 'medium', 'high'] | None = None
    enabled: bool = True

    @model_validator(mode='after')
    def endpoint(self):
        if self.provider in ('custom_openai', 'ollama') and not self.base_url:
            raise ValueError('A base URL is required for this provider.')
        if self.provider in SUBSCRIPTION_PROVIDERS:
            if self.api_key:
                raise ValueError('A subscription login signs in through its own command line tool, not an API key.')
            if self.base_url:
                raise ValueError('A subscription login has no endpoint to configure.')
            # The subscription already covers usage, so the per-token rate is zero rather than
            # unknown.
            self.input_price = self.output_price = 0.0
        elif self.account:
            raise ValueError('Only a subscription login has a named account.')
        if self.capabilities:
            from .adaptive import CAPABILITIES
            if any(k not in CAPABILITIES for k in self.capabilities) or any(not 0 <= v <= 10 for v in self.capabilities.values()):
                raise ValueError('Capabilities are ' + ', '.join(CAPABILITIES) + ', each 0 to 10.')
        if self.base_url:
            u = urlparse(self.base_url)
            if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError('Use an HTTP(S) endpoint without credentials, query, or fragment.')
            if self.provider in ('openai', 'anthropic', 'typesafe'):
                raise ValueError('Use OpenAI Compatible for a custom endpoint.')
        return self

class TenantInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    environment: str = Field(default='Development', max_length=50)
    # The small model Adaptive asks to classify a request, when one is set: any keyed OpenAI-
    # compatible or Anthropic model, a local Ollama model being the intended case. Empty means
    # deterministic heuristics only, which is the fallback whenever the classifier fails anyway.
    router_model_id: str | None = None

class TenantContext(TenantInput):
    tenant_id: str

class LoginInput(StrictModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=200)

class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    root: str | None = Field(default=None, max_length=1000)

class SessionInput(StrictModel):
    name: str = Field(default='New session', min_length=1, max_length=120)

class InstructionInput(StrictModel):
    content: str = Field(min_length=2, max_length=40000)
    model_id: str = Field(min_length=1)
    mode: Mode = 'edit'

class CommandInput(StrictModel):
    command: str = Field(min_length=1, max_length=300)
