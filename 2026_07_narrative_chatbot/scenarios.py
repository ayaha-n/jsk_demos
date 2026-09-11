"""Registry of interchangeable narrative scenarios.

Each scenario bundles an initial state with the full-turn examples used to
compile the DSPy agent for that state. ``AnalyzeInteraction``/``PlanMishearing``/
``GeneratePoohResponse`` and the mishearing gimmick stay shared across
scenarios; only the story content (``NarrativeSituation`` and full-turn
examples) changes per scenario.
"""

from __future__ import annotations

from dataclasses import dataclass

import dspy

from narrative_state import NarrativeSituation


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    initial_situation: NarrativeSituation
    trainset: list[dspy.Example]
    mode_examples: list[dspy.Example]
    mishearing_examples: list[dspy.Example]
    response_examples: list[dspy.Example]


def _tea_party() -> Scenario:
    from pooh_examples import (
        INITIAL_SITUATION,
        MISHEARING_EXAMPLES,
        MODE_EXAMPLES,
        RESPONSE_EXAMPLES,
        TRAINSET,
    )

    return Scenario(
        key="tea_party",
        label="森のお茶会",
        initial_situation=INITIAL_SITUATION,
        trainset=TRAINSET,
        mode_examples=MODE_EXAMPLES,
        mishearing_examples=MISHEARING_EXAMPLES,
        response_examples=RESPONSE_EXAMPLES,
    )


def _eeyore_birthday() -> Scenario:
    from pooh_eeyore_examples import (
        EEYORE_BIRTHDAY_MODE_EXAMPLES,
        EEYORE_BIRTHDAY_RESPONSE_EXAMPLES,
        EEYORE_BIRTHDAY_SITUATION,
        EEYORE_BIRTHDAY_TRAINSET,
    )
    from pooh_examples import MISHEARING_EXAMPLES

    return Scenario(
        key="eeyore_birthday",
        label="イーヨーの誕生日プレゼント",
        initial_situation=EEYORE_BIRTHDAY_SITUATION,
        trainset=EEYORE_BIRTHDAY_TRAINSET,
        mode_examples=EEYORE_BIRTHDAY_MODE_EXAMPLES,
        mishearing_examples=MISHEARING_EXAMPLES,
        response_examples=EEYORE_BIRTHDAY_RESPONSE_EXAMPLES,
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
