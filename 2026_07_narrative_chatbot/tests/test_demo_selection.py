"""Demo selection tests for compiled stages; no LLM/API calls."""

import unittest
from types import SimpleNamespace

# Reuse the regression tests' dspy stub so the import order of test modules
# never decides whether the real dspy or the stub is loaded.
from test_pooh_narrative_dspy import pooh


def response_demo(history: str, bot_response: str, user_action: str = "", scene: str = "none"):
    return SimpleNamespace(
        history=history,
        world_event="",
        bot_response=bot_response,
        user_action=user_action,
        current_scene=scene,
    )


class DemoSelectionTests(unittest.TestCase):
    def test_phrasing_variants_take_one_response_slot(self):
        variants = [response_demo("h", "ぼくはハチミツがいいな", text) for text in ("うーん", "迷うな")]
        other = response_demo("h2", "風船、いいね")
        predictor = SimpleNamespace(demos=[])
        module = SimpleNamespace(predictors=lambda: [predictor])

        pooh.merge_module_demos(module, variants + [other], pooh.RESPOND_DEMO_IDENTITY)

        self.assertEqual(predictor.demos, [variants[0], other])

    def test_labeled_example_behind_a_trace_and_its_variants_are_not_repeated(self):
        # A trace holds the model's own wording, so it differs from the gold
        # response of the example it was bootstrapped from.
        trace = response_demo("h", "うん、そうしよう！", "そうだね")
        source = response_demo("h", "そうしよう", "そうだね")
        variant = response_demo("h", "そうしよう", "うんうん")
        other = response_demo("h2", "青にしよう")
        predictor = SimpleNamespace(demos=[trace, trace])
        module = SimpleNamespace(predictors=lambda: [predictor])

        pooh.merge_module_demos(
            module, [source, variant, other],
            pooh.RESPOND_DEMO_IDENTITY, pooh.RESPOND_DEMO_INPUTS,
        )

        self.assertEqual(predictor.demos, [trace, other])

    def test_classify_stage_keeps_distinct_phrasings(self):
        demos = [
            SimpleNamespace(user_action=text, history="h") for text in ("うーん", "迷うな", "うーん")
        ]
        predictor = SimpleNamespace(demos=[])
        module = SimpleNamespace(predictors=lambda: [predictor])

        pooh.merge_module_demos(module, demos, pooh.CLASSIFY_DEMO_IDENTITY)

        self.assertEqual([demo.user_action for demo in predictor.demos], ["うーん", "迷うな"])

    def test_bootstrap_order_alternates_scenes_and_defers_variants(self):
        a1 = response_demo("a", "A", "a-1", "1a")
        a1_variant = response_demo("a", "A", "a-2", "1a")
        a2 = response_demo("b", "B", "b", "1a")
        c1 = response_demo("c", "C", "c", "3")
        d1 = response_demo("d", "D", "d", "6")

        ordered = pooh.bootstrap_order([a1, a1_variant, a2, c1, d1])

        self.assertEqual(ordered, [a1, c1, d1, a2, a1_variant])

    def test_real_eeyore_bootstrap_order_reaches_several_scenes_early(self):
        scenario = pooh.get_scenario("eeyore_birthday")

        head = pooh.bootstrap_order(scenario.trainset)[:6]

        self.assertGreaterEqual(len({item.current_scene for item in head}), 5)
        self.assertEqual(
            sorted(map(id, pooh.bootstrap_order(scenario.trainset))),
            sorted(map(id, scenario.trainset)),
        )


if __name__ == "__main__":
    unittest.main()
