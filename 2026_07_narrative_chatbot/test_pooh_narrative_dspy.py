"""LLM/APIを呼ばずに行う最小回帰テスト。"""

import ast
import sys
import types
import unittest
from types import SimpleNamespace
from pathlib import Path


# DSPy未導入の開発環境でも純粋関数と状態定義を検査できる最小stub。
class _Field:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Example:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def with_inputs(self, *args):
        self.inputs = args
        return self

    def toDict(self):
        return vars(self)


class _Module:
    def __init__(self, *args, **kwargs):
        self.demos = []

    def predictors(self):
        return []


fake = types.ModuleType("dspy")
fake.Signature = object
fake.InputField = _Field
fake.OutputField = _Field
fake.Example = _Example
fake.Predict = _Module
fake.ChainOfThought = _Module
fake.LM = _Module
fake.configure = lambda **kwargs: None
fake.context = lambda **kwargs: __import__("contextlib").nullcontext()
teleprompt = types.ModuleType("dspy.teleprompt")
teleprompt.BootstrapFewShot = _Module
sys.modules.setdefault("dspy", fake)
sys.modules.setdefault("dspy.teleprompt", teleprompt)

import pooh_narrative_dspy as pooh


class RegressionTests(unittest.TestCase):
    def test_source_is_valid_python(self):
        ast.parse(Path(pooh.__file__).read_text(encoding="utf-8"))

    def test_initial_state_has_required_blue_balloon_and_context(self):
        for term in ("森の空き地", "青い風船", "テーブル", "蜂蜜壺", "一緒につくる仲間"):
            self.assertIn(term, pooh.INITIAL_SITUATION)

    def test_bot_response_field_forbids_direct_technical_terms(self):
        description = pooh.PoohNarrativeInteraction.bot_response.kwargs["desc"]
        self.assertIn("技術語や技術概念を直接出さない", description)

    def test_all_examples_include_declared_outputs(self):
        for item in pooh.TRAINSET:
            for field in ("interaction_mode", "updated_situation", "bot_response"):
                self.assertTrue(getattr(item, field, "").strip())

    def test_examples_cover_all_interaction_modes(self):
        self.assertEqual(
            {item.interaction_mode for item in pooh.TRAINSET},
            {"narrative", "ordinary", "meta", "exit"},
        )

    def test_meta_followup_example_keeps_meta_mode(self):
        matches = [item for item in pooh.TRAINSET if item.user_action == "サーボのことだよ"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].interaction_mode, "meta")
        self.assertIn("応答モード: meta", matches[0].history)
        self.assertNotIn("サーボ", matches[0].bot_response)

    def test_history_retains_mode_and_updated_state(self):
        turn = pooh.Turn("行為", "ordinary", "応答", "状態")
        history = pooh.format_history([turn])
        for value in ("行為", "ordinary", "応答", "状態"):
            self.assertIn(value, history)

    def test_meta_policy_is_a_hard_gate(self):
        scores = SimpleNamespace(
            mode_accuracy=5,
            response_fit=5,
            narrative_coherence=5,
            participant_agency=5,
            state_quality=5,
        )
        judge = lambda **kwargs: scores
        captured = {}
        def violating(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(policy_compliance=3)
        metric = pooh.make_metric(judge, violating, object())
        gold = SimpleNamespace(
            current_situation="状態",
            user_action="ロボット？",
            history="",
            interaction_mode="meta",
            updated_situation="状態",
            bot_response="ロバ？",
        )
        pred = SimpleNamespace(
            interaction_mode="meta",
            updated_situation="状態",
            bot_response="ぼくはロボットではないよ。",
        )
        self.assertEqual(metric(gold, pred), 0.0)
        self.assertNotIn("candidate_situation", captured)

    def test_meta_examples_do_not_repeat_technical_term_in_response(self):
        terms = {
            "これロボットだよね？": "ロボット",
            "モータ何使っているの？": "モータ",
            "アクチュエータは何を使っているの？": "アクチュエータ",
            "プディングじゃなくて、部品。中に何が入ってるの？": "部品",
            "サーボのことだよ": "サーボ",
        }
        for item in pooh.TRAINSET:
            term = terms.get(item.user_action)
            if term:
                self.assertNotIn(term, item.bot_response)

    def test_non_meta_does_not_call_meta_evaluator(self):
        scores = SimpleNamespace(
            mode_accuracy=5,
            response_fit=5,
            narrative_coherence=5,
            participant_agency=5,
            state_quality=5,
        )
        judge = lambda **kwargs: scores
        def unexpected(**kwargs):
            self.fail("meta evaluator called for non-meta example")
        metric = pooh.make_metric(judge, unexpected, object())
        gold = SimpleNamespace(
            current_situation="状態",
            user_action="こんにちは",
            history="",
            interaction_mode="ordinary",
            updated_situation="状態",
            bot_response="こんにちは。",
        )
        pred = SimpleNamespace(
            interaction_mode="ordinary",
            updated_situation="状態",
            bot_response="こんにちは。",
        )
        self.assertEqual(metric(gold, pred), 1.0)

    def test_cache_changes_with_model_or_judge(self):
        self.assertNotEqual(pooh.cache_hash("model-a", "judge"), pooh.cache_hash("model-b", "judge"))
        self.assertNotEqual(pooh.cache_hash("model", "judge-a"), pooh.cache_hash("model", "judge-b"))

    def test_score_is_bounded(self):
        self.assertEqual(pooh.clamp_score(9), 5)
        self.assertEqual(pooh.clamp_score("0"), 1)
        self.assertEqual(pooh.clamp_score("bad"), 1)


if __name__ == "__main__":
    unittest.main()
