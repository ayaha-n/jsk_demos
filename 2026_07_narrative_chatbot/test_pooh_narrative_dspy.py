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
fake.Module = _Module
fake.Prediction = lambda **kwargs: SimpleNamespace(**kwargs)
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
from mishearing_cases import (
    MISHEARING_CASES,
    UNCERTAIN_RESPONSE_TEMPLATES,
    make_uncertain_echo,
    normalize_term,
    uncertain_candidate_for,
)


class RegressionTests(unittest.TestCase):
    def test_source_is_valid_python(self):
        ast.parse(Path(pooh.__file__).read_text(encoding="utf-8"))

    def test_initial_state_has_required_blue_balloon_and_context(self):
        for term in ("森の空き地", "青い風船", "テーブル", "蜂蜜壺", "一緒につくる仲間"):
            self.assertIn(term, pooh.INITIAL_SITUATION)

    def test_bot_response_field_forbids_direct_technical_terms(self):
        description = pooh.GeneratePoohResponse.bot_response.kwargs["desc"]
        self.assertIn("物語世界外の技術語全体を直接出さない", description)
        self.assertIn("不確かな短い音", description)

    def test_all_examples_include_declared_outputs(self):
        for item in pooh.TRAINSET:
            for field in ("interaction_mode", "selected_mishearing", "updated_situation", "bot_response"):
                self.assertTrue(getattr(item, field, "").strip())

    def test_known_technical_terms_handle_long_vowel_variation(self):
        self.assertEqual(
            pooh.find_known_technical_terms("モータが入っているの？"),
            ["モーター"],
        )
        self.assertEqual(pooh.find_known_technical_terms("ネジも入ってる"), ["ねじ"])
        self.assertEqual(pooh.find_known_technical_terms("こんにちは"), [])

    def test_pipeline_datasets_use_separate_schemas(self):
        self.assertEqual(len(pooh.MODE_EXAMPLES), len(pooh.TRAINSET))
        self.assertEqual(len(pooh.RESPONSE_EXAMPLES), len(pooh.TRAINSET))
        self.assertEqual(len(pooh.MISHEARING_EXAMPLES), 3)
        self.assertEqual(
            pooh.MODE_EXAMPLES[0].inputs,
            ("current_situation", "user_action", "history"),
        )
        self.assertIn("mishearing_candidates", pooh.RESPONSE_EXAMPLES[0].inputs)
        robot = next(item for item in pooh.MODE_EXAMPLES if "ロボット" in item.user_action)
        self.assertEqual(robot.technical_terms, ["ロボット"])
        for item in pooh.MISHEARING_EXAMPLES:
            self.assertTrue(item.technical_terms)
            self.assertTrue(item.candidates)

    def test_examples_cover_all_interaction_modes(self):
        self.assertEqual(
            {item.interaction_mode for item in pooh.TRAINSET},
            {"narrative", "ordinary", "meta", "exit"},
        )

    def test_compound_technical_question_is_meta(self):
        matches = [
            item for item in pooh.TRAINSET
            if item.user_action == "ロボットなのに食事ができるの？"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].interaction_mode, "meta")
        self.assertEqual(matches[0].selected_mishearing, "ロバ")
        self.assertNotIn("ロボット", matches[0].bot_response)

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

    def test_mode_mismatch_is_a_hard_gate(self):
        def unexpected(**kwargs):
            self.fail("semantic evaluator called after mode mismatch")
        metric = pooh.make_metric(unexpected, unexpected, object())
        gold = SimpleNamespace(
            current_situation="状態", user_action="ロボット？", history="",
            interaction_mode="meta", selected_mishearing="ロバ",
            updated_situation="状態", bot_response="ロバ？",
        )
        pred = SimpleNamespace(
            interaction_mode="ordinary", selected_mishearing="none",
            updated_situation="状態", bot_response="こんにちは。",
        )
        self.assertEqual(metric(gold, pred), 0.0)

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
        for item, mode_example in zip(pooh.TRAINSET, pooh.MODE_EXAMPLES):
            if item.interaction_mode != "meta":
                continue
            self.assertFalse(
                pooh.contains_technical_term(
                    item.bot_response,
                    mode_example.technical_terms,
                ),
                msg=f"技術語を含む応答です: {item.user_action}",
            )

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

    def test_uncertain_echo_is_short_and_changes_the_term(self):
        expected = {
            "ロボット": "ロボ",
            "モーター": "モタ",
            "サーボ": "サボ",
        }
        for technical_term, expected_echo in expected.items():
            echo = make_uncertain_echo(technical_term)
            self.assertEqual(echo, expected_echo)
            self.assertLessEqual(len(echo), 3)
            self.assertNotEqual(
                normalize_term(echo),
                normalize_term(technical_term),
            )
            candidate = uncertain_candidate_for(technical_term)
            allowed = {
                template.format(echo=echo)
                for template in UNCERTAIN_RESPONSE_TEMPLATES
            }
            self.assertIn(candidate.possible_response, allowed)

    def test_prior_technical_mentions_count_user_input_only(self):
        history = (
            "Turn 1\n参加者の生入力: ロボットなの？\n応答モード: meta\n"
            "プーの応答: ロバ？\n"
            "Turn 2\n参加者の生入力: ロボットのことだよ\n応答モード: meta"
        )
        self.assertEqual(
            pooh.count_prior_technical_mentions(history, ["ロボット"]),
            2,
        )
        self.assertEqual(
            pooh.count_prior_technical_mentions(history, ["モーター"]),
            0,
        )

    def test_reviewed_mishearing_dictionary(self):
        expressions = [case.technical_expression for case in MISHEARING_CASES]
        self.assertEqual(len(expressions), len(set(expressions)))

        for case in MISHEARING_CASES:
            self.assertIn(
                case.technical_expression,
                pooh.find_known_technical_terms(case.technical_expression),
            )
            candidates, unknown = pooh.known_candidates_for(
                [case.technical_expression]
            )
            self.assertEqual(
                [candidate.heard_as for candidate in candidates],
                [case.misheard_word],
            )
            self.assertEqual(unknown, [])

    def test_stage_demos_fill_budget_after_bootstrapping(self):
        augmented = object()
        labeled = [object(), object(), object()]
        predictor = SimpleNamespace(demos=[augmented])
        module = SimpleNamespace(predictors=lambda: [predictor])

        pooh.merge_module_demos(module, labeled)

        self.assertEqual(len(predictor.demos), len(labeled))
        self.assertIs(predictor.demos[0], augmented)
        self.assertEqual(predictor.demos[1:], labeled[:2])

    def test_cache_changes_with_mishearing_examples(self):
        before = pooh.cache_hash("model", "judge")
        example = pooh.MISHEARING_EXAMPLES.pop()
        try:
            self.assertNotEqual(before, pooh.cache_hash("model", "judge"))
        finally:
            pooh.MISHEARING_EXAMPLES.append(example)

    def test_cache_changes_with_model_or_judge(self):
        self.assertNotEqual(pooh.cache_hash("model-a", "judge"), pooh.cache_hash("model-b", "judge"))
        self.assertNotEqual(pooh.cache_hash("model", "judge-a"), pooh.cache_hash("model", "judge-b"))

    def test_score_is_bounded(self):
        self.assertEqual(pooh.clamp_score(9), 5)
        self.assertEqual(pooh.clamp_score("0"), 1)
        self.assertEqual(pooh.clamp_score("bad"), 1)


if __name__ == "__main__":
    unittest.main()
