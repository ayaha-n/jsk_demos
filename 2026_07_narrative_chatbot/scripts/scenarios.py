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

from narrative_events import fixed_utterance
from narrative_state import NarrativeSituation


# オープニングを話し終えてから、プーが蜂蜜壺を贈ると決めるまでの秒数。
EEYORE_GIFT_DECISION_DELAY_SECONDS = 30.0
# 贈ると決めてから、蜂蜜壺を持ち出すまでの秒数。
EEYORE_HONEY_TASTING_DELAY_SECONDS = 30.0
# 蜂蜜壺を持ち出してから、蜂蜜を食べてしまうまでの秒数。
EEYORE_HONEY_EATING_DELAY_SECONDS = 10.0
# 場面の始まりになるイベント（贈る決定・持ち出し）は、期限を過ぎても最後の
# やり取りからこの秒数の沈黙を待つ。会話が続く場合は、次の返事に続けて発火する。
EEYORE_EVENT_QUIET_SECONDS = 12.0
# プーの直前のセリフが参加者への問いかけで答えを待っている間は、この秒数まで待つ。
EEYORE_AWAITING_REPLY_QUIET_SECONDS = 30.0
# 贈り物が決まった締めのあと、この秒数の沈黙が続いたら終わりのセリフで終える。
# 参加者が話し続けている間は終えない。
EEYORE_IDLE_CLOSE_AFTER_WRAP_UP_SECONDS = 30.0


@dataclass(frozen=True)
class SceneDefinition:
    """A display-only scene inferred by DSPy; never used for control flow."""

    scene_id: str
    label: str


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    # Display-only prose shown by the Web UI; never used for event control or
    # passed to DSPy as narrative state.
    scene_intro: str
    initial_situation: NarrativeSituation
    opening_line: str
    ending_line: str
    trainset: list[dspy.Example]
    mode_examples: list[dspy.Example]
    mishearing_examples: list[dspy.Example]
    response_examples: list[dspy.Example]
    interpret_examples: list[dspy.Example]
    scenes: tuple[SceneDefinition, ...] = ()
    gift_decision_delay_seconds: float | None = None
    honey_tasting_delay_seconds: float | None = None
    honey_eating_delay_seconds: float | None = None
    event_quiet_seconds: float = 0.0
    awaiting_reply_quiet_seconds: float | None = None
    idle_close_after_wrap_up_seconds: float | None = None
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
        INTERPRET_EXAMPLES,
        MISHEARING_EXAMPLES,
        MODE_EXAMPLES,
        RESPONSE_EXAMPLES,
        TEA_PARTY_OPENING_LINE,
        TRAINSET,
    )

    return Scenario(
        key="tea_party",
        label="森のお茶会",
        scene_intro=(
            "森の空き地。テーブルのところにプーが座っています。"
            "テーブルの上には、カップとお皿、ハチミツの入った壺。"
            "そばには青い風船があります。"
        ),
        initial_situation=INITIAL_SITUATION,
        opening_line=TEA_PARTY_OPENING_LINE,
        ending_line=fixed_utterance("tea_party.ending"),
        trainset=TRAINSET,
        mode_examples=MODE_EXAMPLES,
        mishearing_examples=MISHEARING_EXAMPLES,
        response_examples=RESPONSE_EXAMPLES,
        interpret_examples=INTERPRET_EXAMPLES,
    )


def _eeyore_birthday() -> Scenario:
    from pooh_eeyore_examples import (
        EEYORE_BIRTHDAY_INTERPRET_EXAMPLES,
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
        scene_intro=(
            "100エーカーの森の空き地。テーブルのところにプーが座っています。"
            "テーブルの上には、カップとお皿、ハチミツの入った壺。"
            "そばには色とりどりの風船とリボンがあります。"
        ),
        initial_situation=EEYORE_BIRTHDAY_SITUATION,
        opening_line=EEYORE_BIRTHDAY_OPENING_LINE,
        ending_line=fixed_utterance("eeyore_birthday.ending"),
        trainset=EEYORE_BIRTHDAY_TRAINSET,
        mode_examples=EEYORE_BIRTHDAY_MODE_EXAMPLES,
        mishearing_examples=MISHEARING_EXAMPLES,
        response_examples=EEYORE_BIRTHDAY_RESPONSE_EXAMPLES,
        interpret_examples=EEYORE_BIRTHDAY_INTERPRET_EXAMPLES,
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
        gift_decision_delay_seconds=EEYORE_GIFT_DECISION_DELAY_SECONDS,
        honey_tasting_delay_seconds=EEYORE_HONEY_TASTING_DELAY_SECONDS,
        honey_eating_delay_seconds=EEYORE_HONEY_EATING_DELAY_SECONDS,
        event_quiet_seconds=EEYORE_EVENT_QUIET_SECONDS,
        awaiting_reply_quiet_seconds=EEYORE_AWAITING_REPLY_QUIET_SECONDS,
        idle_close_after_wrap_up_seconds=EEYORE_IDLE_CLOSE_AFTER_WRAP_UP_SECONDS,
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
