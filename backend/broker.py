"""Only this module knows provider SDKs. No global credentials or implicit environment routing.

Subscription providers are the single deliberate exception to that second rule: a locally
installed agent CLI signs in with the user's own Claude or ChatGPT subscription, so those
credentials live outside this application and are never seen by it. The exception is confined
here, and the CLI is launched with its tools disabled and its sandbox read-only, so it still
only returns an artifact. Frontier alone decides what reaches a project, through the reviewer
and the workflow's execution mode.
"""
from dataclasses import dataclass
import asyncio
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic, transform_schema
from .localprocess import child_env, child_flags, terminate
from .store import Store

# Closed contracts can use provider-constrained decoding. The planner's free-form dependency
# maps stay locally validated, because closing those maps would discard data.
STRICT_CONTRACTS = ('BuildArtifact', 'ReviewReport')
# The package entry point keeps a shim-only npm install usable: create_subprocess_exec cannot
# execute a .cmd, and generated text must never be routed through cmd.exe.
CLI_TOOLS = {
    'claude_cli': ('claude', 'node_modules/@anthropic-ai/claude-code/cli.js', 'Claude Code'),
    'codex_cli': ('codex', 'node_modules/@openai/codex/bin/codex.js', 'Codex'),
}

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

def resolve_cli(provider):
    name, entry, label = CLI_TOOLS[provider]
    found = shutil.which(name)
    if found and Path(found).suffix.lower() not in ('.cmd', '.bat', '.ps1'):
        return [found]
    node = shutil.which('node')
    roots = ([Path(found).parent] if found else []) + ([Path(os.environ['APPDATA'])/'npm'] if os.environ.get('APPDATA') else [])
    for root in roots:
        script = root/entry
        if node and script.is_file():
            return [node, str(script)]
    raise ProviderError(f'The {label} command line tool was not found. Install it, sign in to your subscription, then test the connection.')

def strict_schema(schema):
    """Copy a schema into the form OpenAI structured output accepts.

    Every property of an object must appear in `required` there, while Pydantic omits any
    property that has a default. Open maps carry no `properties` and are left alone.
    """
    def close(node):
        if isinstance(node, dict):
            if node.get('type') == 'object' and 'properties' in node:
                node['required'] = list(node['properties'])
                node['additionalProperties'] = False
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for item in node:
                close(item)
        return node
    return close(json.loads(json.dumps(schema)))

async def run_cli(argv, stdin_text, work):
    """Run an agent CLI to completion, killing its whole tree on timeout or cancellation."""
    proc = await asyncio.create_subprocess_exec(*argv, cwd=work, env=child_env(),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **child_flags())
    try:
        out, _ = await proc.communicate(stdin_text.encode('utf-8') if stdin_text is not None else None)
    finally:
        await terminate(proc)
    return proc.returncode, out.decode('utf-8', errors='replace')

async def probe_cli(provider):
    """Confirm the CLI runs, without a model call. Sign-in is only proven by a real request."""
    with TemporaryDirectory(prefix='frontier-cli-') as directory:
        code, out = await run_cli([*resolve_cli(provider), '--version'], None, directory)
    if code != 0:
        raise ProviderError(f'The {CLI_TOOLS[provider][2]} command line tool did not run. Reinstall it, then test again.')
    return out.strip()[:80]

class ModelBroker:
    def __init__(self, store: Store):
        self.store = store

    async def invoke_cli(self, config, system, prompt, schema):
        """Ask a signed-in agent CLI for one artifact.

        Tools are off and the sandbox is read-only, so the CLI cannot reach the project even
        though it runs locally. Per-stage token and temperature limits have no equivalent flag
        and are not applied here.
        """
        provider = config['provider']
        launch = resolve_cli(provider)
        with TemporaryDirectory(prefix='frontier-cli-') as directory:
            work = Path(directory)
            if provider == 'claude_cli':
                system_file = work/'system.txt'
                system_file.write_text(system, encoding='utf-8')
                argv = [*launch, '-p', '--model', config['model_name'], '--output-format', 'json',
                        '--tools', '', '--permission-mode', 'dontAsk', '--no-session-persistence',
                        '--system-prompt-file', str(system_file)]
                stdin_text = prompt
            else:
                final = work/'final.txt'
                argv = [*launch, '-a', 'never', 'exec', '--ephemeral', '--skip-git-repo-check',
                        '--sandbox', 'read-only', '--model', config['model_name'],
                        '--output-last-message', str(final)]
                if schema and schema.get('title') in STRICT_CONTRACTS:
                    schema_file = work/'schema.json'
                    schema_file.write_text(json.dumps(strict_schema(schema)), encoding='utf-8')
                    argv += ['--output-schema', str(schema_file)]
                argv.append('-')
                # Codex has no system prompt flag, so the role instructions lead the input.
                stdin_text = 'SYSTEM INSTRUCTIONS:\n' + system + '\n\n' + prompt
            code, out = await run_cli(argv, stdin_text, work)
            if code != 0:
                # Stderr can echo the prompt, so it is never surfaced or stored.
                raise ProviderError(f'The {CLI_TOOLS[provider][2]} command line tool exited with code {code}. Run it once in a terminal to confirm the subscription is signed in.')
            if provider == 'codex_cli':
                text = final.read_text(encoding='utf-8') if final.is_file() else ''
                if not text.strip():
                    raise ProviderError(f'{CLI_TOOLS[provider][2]} returned no final message. Confirm the subscription is signed in.')
                # Codex reports only a combined token total, so usage stays unreported.
                return ModelResult(text, None, None)
        try:
            payload = json.loads(out or '{}')
        except json.JSONDecodeError:
            raise ProviderError(f'{CLI_TOOLS[provider][2]} returned output that could not be read.') from None
        usage = payload.get('usage') or {}
        error = None
        if payload.get('is_error'):
            error = f'{CLI_TOOLS[provider][2]} reported an error for this request.'
        elif payload.get('stop_reason') == 'max_tokens':
            error = 'The model reached its output token limit. Increase the stage token limit.'
        return ModelResult(payload.get('result') or '', usage.get('input_tokens'), usage.get('output_tokens'), error)

    async def invoke(self, tenant_id, model_id, system, prompt, stage, schema=None):
        config = self.store.get(tenant_id, 'models', model_id)
        key = self.store.decrypt(config['encrypted_key']) if config.get('encrypted_key') else 'local-no-key'
        if schema:
            system += '\nReturn ONLY a JSON object matching this schema, without Markdown fences:\n' + json.dumps(schema)
        options = {'temperature': stage['temperature']} if stage.get('temperature') is not None else {}
        try:
            async with asyncio.timeout(stage['timeout']):
                if config['provider'] in CLI_TOOLS:
                    return await self.invoke_cli(config, system, prompt, schema)
                if config['provider'] == 'anthropic':
                    async with AsyncAnthropic(api_key=key, max_retries=0) as client:
                        strict = bool(schema and schema.get('title') in STRICT_CONTRACTS)
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
