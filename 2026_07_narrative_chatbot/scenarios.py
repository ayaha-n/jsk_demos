"""Registry of interchangeable narrative scenarios.

Each scenario bundles an initial state with the full-turn examples used to
compile the DSPy agent for that state. ``AnalyzeInteraction``/``PlanMishearing``/
``GeneratePoohResponse`` and the mishearing gimmick stay shared across
scenarios; only the story content (``NarrativeSituation`` and full-turn
examples) changes per scenario.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import dspy

from narrative_state import NarrativeSituation


# 蜂蜜壺を贈ると決めてから、プーが蜂蜜を食べてしまうまでの秒数。
EEYORE_EVENT_INACTIVITY_DELAY_SECONDS = 30.0


@dataclass(frozen=True)
class SceneDefinition:
    """A display-only scene inferred by DSPy; never used for control flow."""

    scene_id: str
    label: str


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    initial_situation: NarrativeSituation
    opening_line: str
    trainset: list[dspy.Example]
    mode_examples: list[dspy.Example]
    mishearing_examples: list[dspy.Example]
    response_examples: list[dspy.Example]
    scenes: tuple[SceneDefinition, ...] = ()
    event_inactivity_delay_seconds: float | None = None
    # Keyed by the unresolved-item label the preference answers (e.g. the
    # balloon-color unresolved text); only entries matching the CURRENT
    # unresolved list are ever surfaced to the model (see
    # narrative_state.relevant_preferences).
    pooh_preferences: dict[str, str] = field(default_factory=dict)

    def scene_label(self, scene_id: str) -> str | None:
        return next(
            (scene.label for scene in self.scenes if scene.scene_id == scene_id),
            None,
        )


def _tea_party() -> Scenario:
    from pooh_examples import (
        INITIAL_SITUATION,
        MISHEARING_EXAMPLES,
        MODE_EXAMPLES,
        RESPONSE_EXAMPLES,
        TEA_PARTY_OPENING_LINE,
        TRAINSET,
    )

    return Scenario(
        key="tea_party",
        label="森のお茶会",
        initial_situation=INITIAL_SITUATION,
        opening_line=TEA_PARTY_OPENING_LINE,
        trainset=TRAINSET,
        mode_examples=MODE_EXAMPLES,
        mishearing_examples=MISHEARING_EXAMPLES,
        response_examples=RESPONSE_EXAMPLES,
    )


def _eeyore_birthday() -> Scenario:
    from pooh_eeyore_examples import (
        EEYORE_BIRTHDAY_MODE_EXAMPLES,
        EEYORE_BIRTHDAY_OPENING_LINE,
        EEYORE_BIRTHDAY_POOH_PREFERENCES,
        EEYORE_BIRTHDAY_RESPONSE_EXAMPLES,
        EEYORE_BIRTHDAY_SITUATION,
        EEYORE_BIRTHDAY_TRAINSET,
    )
    from pooh_examples import MISHEARING_EXAMPLES

    return Scenario(
        key="eeyore_birthday",
        label="イーヨーの誕生日プレゼント",
        initial_situation=EEYORE_BIRTHDAY_SITUATION,
        opening_line=EEYORE_BIRTHDAY_OPENING_LINE,
        trainset=EEYORE_BIRTHDAY_TRAINSET,
        mode_examples=EEYORE_BIRTHDAY_MODE_EXAMPLES,
        mishearing_examples=MISHEARING_EXAMPLES,
        response_examples=EEYORE_BIRTHDAY_RESPONSE_EXAMPLES,
        scenes=(
            SceneDefinition("1a", "場面1a：プーがハチミツを贈ると提案する"),
            SceneDefinition("1b", "場面1b：参加者が別の贈り物を提案する"),
            SceneDefinition("1c", "場面1c：プーが自発的にハチミツを贈ると決める（無入力での自動発生）"),
            SceneDefinition("2", "場面2：一口だけのつもりで持ち出す（伏線・自動発生）"),
            SceneDefinition("3", "場面3：プーが蜂蜜を食べてしまう（自動発生）"),
            SceneDefinition("4a", "場面4a：空になった壺をそのまま贈ることにする"),
            SceneDefinition("4b", "場面4b：蜂蜜以外の贈り物に切り替える"),
            SceneDefinition("5", "場面5：決めた贈り物を振り返る"),
            SceneDefinition("6", "場面6：贈り物の準備を詰める（色などの詳細を相談する）"),
        ),
        event_inactivity_delay_seconds=EEYORE_EVENT_INACTIVITY_DELAY_SECONDS,
        pooh_preferences=EEYORE_BIRTHDAY_POOH_PREFERENCES,
    )


SCENARIOS: dict[str, Scenario] = {
    "tea_party": _tea_party(),
    "eeyore_birthday": _eeyore_birthday(),
}

DEFAULT_SCENARIO = "tea_party"


def get_scenario(key: str) -> Scenario:
    try:
        return SCENARIOS[key]
    except KeyError as exc:
        available = "、".join(sorted(SCENARIOS))
        raise ValueError(f"未知のシナリオです: {key}（利用可能: {available}）") from exc
