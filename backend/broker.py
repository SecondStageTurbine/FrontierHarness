"""Only this module knows provider SDKs. No global credentials or implicit environment routing."""
from dataclasses import dataclass
import asyncio
import json
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic, transform_schema
from .store import Store

class ProviderError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable

@dataclass
class ModelResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    error: str | None = None

class ModelBroker:
    def __init__(self, store: Store):
        self.store = store

    async def invoke(self, tenant_id, model_id, system, prompt, stage, schema=None):
        config = self.store.get(tenant_id, 'models', model_id)
        key = self.store.decrypt(config['encrypted_key']) if config.get('encrypted_key') else 'local-no-key'
        if schema:
            system += '\nReturn ONLY a JSON object matching this schema, without Markdown fences:\n' + json.dumps(schema)
        options = {'temperature': stage['temperature']} if stage.get('temperature') is not None else {}
        try:
            async with asyncio.timeout(stage['timeout']):
                if config['provider'] == 'anthropic':
                    async with AsyncAnthropic(api_key=key, max_retries=0) as client:
                        # Closed build/review contracts can use provider-constrained
                        # decoding. The planner's free-form dependency maps remain
                        # validated locally; closing those maps would discard data.
                        strict = bool(schema and schema.get('title') in ('BuildArtifact','ReviewReport'))
                        tool_schema = transform_schema(schema) if strict else schema
                        structured = {'tools':[{'name':'submit_artifact','description':'Submit the requested structured artifact. This records data only; it executes no external action.','input_schema':tool_schema,**({'strict':True} if strict else {})}], 'tool_choice':{'type':'tool','name':'submit_artifact','disable_parallel_tool_use':True}} if schema else {}
                        r = await client.messages.create(model=config['model_name'], max_tokens=stage['max_tokens'], system=system, messages=[{'role': 'user', 'content': prompt}], **structured, **options)
                        tool = next((c for c in r.content if c.type=='tool_use' and c.name=='submit_artifact'),None)
                        text = json.dumps(tool.input) if tool else ''.join(c.text for c in r.content if c.type == 'text')
                        return ModelResult(text, r.usage.input_tokens, r.usage.output_tokens, 'The model reached its output token limit. Increase the stage token limit.' if r.stop_reason=='max_tokens' else None)
                async with AsyncOpenAI(api_key=key, base_url=config.get('base_url') or 'https://api.openai.com/v1', max_retries=0) as client:
                    if config['provider'] == 'openai':
                        r = await client.responses.create(model=config['model_name'], instructions=system, input=prompt, max_output_tokens=stage['max_tokens'], store=False, **options)
                        return ModelResult(r.output_text, r.usage.input_tokens if r.usage else None, r.usage.output_tokens if r.usage else None, 'The model response was incomplete. Check the token limit and provider configuration.' if r.status!='completed' else None)
                    r = await client.chat.completions.create(model=config['model_name'], messages=[{'role':'system','content':system},{'role':'user','content':prompt}], max_tokens=stage['max_tokens'], **options)
                    if not r.choices or r.choices[0].finish_reason not in ('stop', None):
                        raise ProviderError('The endpoint returned an incomplete response. Check the token limit.')
                    return ModelResult(r.choices[0].message.content or '', r.usage.prompt_tokens if r.usage else None, r.usage.completion_tokens if r.usage else None)
        except ProviderError:
            raise
        except TimeoutError:
            raise ProviderError('The model timed out. Retry the run or increase the stage timeout.', True) from None
        except Exception as exc:
            # Provider exception strings can contain request payloads or credentials. Never persist them.
            status = getattr(exc, 'status_code', None)
            messages = {401:'Provider authentication failed. Replace the API key in Models.',403:'The provider denied access to this model.',404:'The provider could not find this model or endpoint.',429:'The provider rate limit or account quota was reached.'}
            raise ProviderError(messages.get(status, 'The provider request failed. Check model access, endpoint, and request settings.'), status in (429,500,502,503,504) or status is None) from None
