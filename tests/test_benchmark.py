"""Public leaderboards set strengths and cost only for models they actually list."""
import json
import httpx
from backend import adaptive, benchmark

DEEPSWE = [
    {'model': 'gpt-6-astra', 'pass': 0.70, 'cost': 3.0, 'effort': 'high'},
    {'model': 'gpt-6-astra', 'pass': 0.74, 'cost': 4.4, 'effort': 'xhigh'},
    {'model': 'gpt-5-6-terra', 'pass': 0.60, 'cost': 1.1},
    {'model': 'claude-opus-4-8', 'pass': 0.59, 'cost': 13.2},
    {'model': 'claude-opus-5', 'pass': 0.74, 'cost': 11.8},
    {'model': 'deepseek-v4-flash', 'pass': 0.53, 'cost': 0.1},
]


def row(org, agent, label, accuracy):
    return {'id': 'x', 'metadata': {'model_org': {'label': org}, 'agent_display': {'label': agent}, 'model_display': {'label': label},
                                    'reasoning_effort': 'max'}, 'metrics': {'accuracy': accuracy}}


# As the page embeds them: escaped JSON inside a script's string.
ROWS = [row('OpenAI', 'Codex', 'GPT-5.6 Terra', 21.5), row('Anthropic', 'Claude Code', 'Fable 5', 44.6), row('Anthropic', 'Claude Code', 'Fable 5.1', 57.9)]
PAGE = '<script>self.__next_f.push([1,"' + json.dumps({'rows': ROWS}).replace('"', '\\"') + '"])</script>'


def saved(tmp_path):
    (tmp_path/'benchmark-deepswe.json').write_text(json.dumps(DEEPSWE), encoding='utf-8')
    rows = benchmark.parse_tbench(httpx.Response(200, text=PAGE))
    (tmp_path/'benchmark-tbench.json').write_text(json.dumps(rows), encoding='utf-8')
    return benchmark.table(tmp_path)


def test_terminal_bench_rows_are_read_from_its_page():
    rows = benchmark.parse_tbench(httpx.Response(200, text=PAGE))
    assert [(r['model'], r['harness']) for r in rows] == [('gpt-5-6-terra', 'Codex'), ('claude-fable-5', 'Claude Code'), ('claude-fable-5-1', 'Claude Code')]
    assert rows[0]['pass'] == 0.215


def test_models_are_matched_by_their_identifier_and_only_when_listed(tmp_path):
    boards = saved(tmp_path)
    find = lambda provider, name: benchmark.match({'provider': provider, 'model_name': name}, boards)
    assert find('codex_cli', 'gpt-6-astra')['deepswe']['pass'] == 0.74  # Its best configuration stands for it.
    assert set(find('codex_cli', 'gpt-5.6-terra')) == {'deepswe', 'tbench'}
    assert find('claude_cli', 'Opus')['deepswe']['model'] == 'claude-opus-5'  # An alias is the newest of its family.
    assert find('claude_cli', 'Fable')['tbench']['model'] == 'claude-fable-5-1'
    assert find('opencode_cli', 'ollama/deepseek-v4-flash:cloud')['deepswe']['model'] == 'deepseek-v4-flash'
    assert find('opencode_cli', 'opencode/nemotron-3-ultra-free') is None
    assert find('codex_cli', 'gpt-6-sol') is None  # Close is not the same model.
    assert find('gemini_cli', 'gemini-3.8-flash-high') is None  # Not on these test boards...
    boards['deepswe']['gemini-3-8-flash'] = {'model': 'gemini-3-8-flash', 'pass': 0.74}
    assert find('gemini_cli', 'gemini-3.8-flash-high')['deepswe']['pass'] == 0.74  # ...found once listed, effort aside.


def test_each_board_sets_its_own_skills_and_the_user_still_wins(tmp_path):
    boards = saved(tmp_path)
    model = {'id': 'x', 'name': 'Terra', 'provider': 'codex_cli', 'model_name': 'gpt-5.6-terra'}
    judged = adaptive.profile({**model, 'benchmark': benchmark.match(model, boards)})
    # DeepSWE 60% speaks to coding; Terminal-Bench 21.5% in Codex to tool use and debugging.
    assert judged['coding'] == judged['repository'] == 8 and judged['tool_use'] == judged['debugging'] == 6
    assert judged['cost_class'] == 'low'
    only = adaptive.profile({**model, 'benchmark': {'deepswe': {'pass': 0.75, 'cost': 1.1}}})
    assert only['coding'] == only['tool_use'] == 10  # One board alone speaks to all four.
    assert adaptive.profile({**model, 'benchmark': {'deepswe': {'pass': 0.75}}, 'capabilities': {'coding': 6}})['coding'] == 6
    free = {'id': 'f', 'name': 'Free', 'provider': 'opencode_cli', 'model_name': 'opencode/deepseek-v4-flash-free'}
    assert adaptive.profile({**free, 'benchmark': {'deepswe': {'pass': 0.53, 'cost': 0.1}}})['cost_class'] == 'free'
    assert benchmark.describe(benchmark.match(model, boards)) == 'DeepSWE 60% of tasks passed, about $1.10 per task; Terminal-Bench 22% in Codex'


def test_a_page_that_changed_shape_is_reported_not_guessed():
    try:
        benchmark.parse_tbench(httpx.Response(200, text='<html>redesigned</html>'))
    except ValueError as error:
        assert 'no longer carries its rows' in str(error)
    else:
        raise AssertionError('expected a ValueError')
