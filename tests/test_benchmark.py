"""The public leaderboard sets coding strengths and cost only for models it actually lists."""
import json
from backend import adaptive, benchmark

ROWS = {'rows': [
    {'model': 'gpt-6-astra', 'pass_at_1': 0.70, 'mean_cost_usd': 3.0, 'reasoning_effort': 'high'},
    {'model': 'gpt-6-astra', 'pass_at_1': 0.74, 'mean_cost_usd': 4.4, 'reasoning_effort': 'xhigh'},
    {'model': 'gpt-5-6-terra', 'pass_at_1': 0.60, 'mean_cost_usd': 1.1},
    {'model': 'claude-opus-4-8', 'pass_at_1': 0.59, 'mean_cost_usd': 13.2},
    {'model': 'claude-opus-5', 'pass_at_1': 0.74, 'mean_cost_usd': 11.8},
    {'model': 'deepseek-v4-flash', 'pass_at_1': 0.53, 'mean_cost_usd': 0.1},
]}


def test_models_are_matched_by_their_identifier_and_only_when_listed(tmp_path):
    (tmp_path/benchmark.CACHE_NAME).write_text(json.dumps(ROWS), encoding='utf-8')
    board = benchmark.table(tmp_path)
    find = lambda provider, name: benchmark.match({'provider': provider, 'model_name': name}, board)
    assert find('codex_cli', 'gpt-6-astra')['pass'] == 0.74  # Its best configuration stands for it.
    assert find('codex_cli', 'gpt-5.6-terra')['model'] == 'gpt-5-6-terra'
    assert find('claude_cli', 'Opus')['model'] == 'claude-opus-5'  # An alias is the newest of its family.
    assert find('opencode_cli', 'ollama/deepseek-v4-flash:cloud')['model'] == 'deepseek-v4-flash'
    assert find('opencode_cli', 'opencode/nemotron-3-ultra-free') is None
    assert find('codex_cli', 'gpt-6-sol') is None  # Close is not the same model.


def test_the_benchmark_sets_coding_skills_and_cost_but_the_user_still_wins():
    model = {'id': 'x', 'name': 'Terra', 'provider': 'codex_cli', 'model_name': 'gpt-5.6-terra'}
    entry = {'model': 'gpt-5-6-terra', 'pass': 0.75, 'cost': 1.1}
    judged = adaptive.profile({**model, 'benchmark': entry})
    assert judged['coding'] == judged['debugging'] == 10 and judged['cost_class'] == 'low'
    assert adaptive.profile({**model, 'benchmark': {**entry, 'pass': 0.35}})['coding'] == 5
    assert adaptive.profile({**model, 'benchmark': entry, 'capabilities': {'coding': 6}})['coding'] == 6
    free = {'id': 'f', 'name': 'Free', 'provider': 'opencode_cli', 'model_name': 'opencode/deepseek-v4-flash-free'}
    assert adaptive.profile({**free, 'benchmark': {'pass': 0.53, 'cost': 0.1}})['cost_class'] == 'free'
    assert benchmark.describe(entry) == 'DeepSWE benchmark 75% of tasks passed, about $1.10 per task'
