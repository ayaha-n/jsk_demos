"""Registry of interchangeable narrative scenarios.

Each scenario bundles an initial state with the full-turn examples used to
compile the DSPy agent for that state. ``AnalyzeInteraction``/``PlanMishearing``/
``GeneratePoohResponse`` and the mishearing gimmick stay shared across
scenarios; only the story content (``NarrativeSituation`` and full-turn
examples) changes per scenario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import dspy

from narrative_events import NarrativeEventController, fixed_utterance
from narrative_state import NarrativeSituation


# オープニングを話し終えてから、プーが蜂蜜壺を贈ると決めるまでの秒数。
EEYORE_GIFT_DECISION_DELAY_SECONDS = 30.0
# 贈ると決めてから、蜂蜜壺を持ち出すまでの秒数。
EEYORE_HONEY_TASTING_DELAY_SECONDS = 45.0
# 蜂蜜壺を持ち出してから、蜂蜜を食べてしまうまでの秒数。
EEYORE_HONEY_EATING_DELAY_SECONDS = 2.0
# 場面の始まりになるイベント（贈る決定・持ち出し）は、期限を過ぎても最後の
# やり取りからこの秒数の沈黙を待つ。会話が続く場合は、次の返事に続けて発火する。
EEYORE_EVENT_QUIET_SECONDS = 12.0
# プーの直前のセリフが参加者への問いかけで答えを待っている間は、この秒数まで待つ。
EEYORE_AWAITING_REPLY_QUIET_SECONDS = 30.0
# 蜂蜜がなくなったあと、空の壺をどうするかも、壺に関係なく何をするかも決まらない
# まま、蜂蜜がなくなったセリフを話し終えてからこの秒数たつと、プーが空の壺を
# どうするか尋ねる。
EEYORE_ASK_EMPTY_JAR_DELAY_SECONDS = 30.0
# 贈り物が決まった締めのあと、この秒数の沈黙が続いたら終わりのセリフで終える。
# 参加者が話し続けている間は終えない。
EEYORE_IDLE_CLOSE_AFTER_WRAP_UP_SECONDS = 30.0

# footprintsは参加者が考えて入力する時間を確保する。各待ち時間は、
# 直前の段階を終えた推定発話終了から数える（自然な会話で済んだ段階は省略）。
# オープニングを話し終えてから、名前を尋ねる固定セリフまでの秒数。
FOOTPRINTS_ASK_NAME_DELAY_SECONDS = 60.0
# 名前を尋ねてから、回答がなければプーが自分で名付けるまでの秒数。
FOOTPRINTS_NAMING_DELAY_SECONDS = 60.0
# 名付けが終わってから、見た目を尋ねる固定セリフまでの秒数。
FOOTPRINTS_ASK_APPEARANCE_DELAY_SECONDS = 20.0
# 見た目を尋ねるか、その相談が自然に済んでから、追跡を始めるまでの秒数。
FOOTPRINTS_START_FOLLOWING_DELAY_SECONDS = 60.0
# 追跡開始から、足跡が２匹分に見えることに気づくまでの秒数。
FOOTPRINTS_SECOND_ANIMAL_DELAY_SECONDS = 30.0
# ２匹に気づいてから、４匹に増えたことに気づくまでの秒数。
FOOTPRINTS_FOURTH_ANIMAL_DELAY_SECONDS = 30.0
# 気づきの誘導を次の段階へ進めるまでの秒数。
FOOTPRINTS_REVEAL_STEP_DELAY_SECONDS = 45.0
FOOTPRINTS_EVENT_QUIET_SECONDS = 12.0
FOOTPRINTS_AWAITING_REPLY_QUIET_SECONDS = 60.0
FOOTPRINTS_IDLE_CLOSE_AFTER_REVEAL_SECONDS = 30.0


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
    # Builds this scenario's timed-event controller given a clock, or None
    # for a scenario with no automatic, time-driven story beats (like
    # tea_party). See narrative_events.NarrativeEventController and
    # eeyore_birthday_events.HoneyGiftEventController for the shape a new
    # scenario's own controller should follow.
    event_controller_factory: (
        Callable[[Callable[[], float]], NarrativeEventController] | None
    ) = None
    # Heard outside the robot's narrative mode, these start the story
    # (``--start-on-mode``): each the words that must all be heard, kept
    # apart from the mode keywords of pooh_body's speech_mode_rules.yaml.
    start_cues: tuple[tuple[str, ...], ...] = ()
    # Keyed by the unresolved-item label the preference answers (e.g. the
    # balloon-color unresolved text); only entries matching the CURRENT
    # unresolved list are ever surfaced to the model (see
    # narrative_state.relevant_preferences).
    pooh_preferences: dict[str, str] = field(default_factory=dict)
    # A scenario's own subclass of AnalyzeInteraction/InterpretTurn/
    # GeneratePoohResponse (narrative_signatures.py), or None to use the
    # generic signature unchanged. See eeyore_birthday_events.py/
    # footprints_events.py for the shape a new scenario's own subclass
    # should follow — defined next to that scenario's other content, never
    # in pooh_narrative_dspy.py (see docs/known_issues.md).
    classify_signature: type[dspy.Signature] | None = None
    interpret_signature: type[dspy.Signature] | None = None
    response_signature: type[dspy.Signature] | None = None

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
        start_cues=(("お茶会", "しよう"),),
        trainset=TRAINSET,
        mode_examples=MODE_EXAMPLES,
        mishearing_examples=MISHEARING_EXAMPLES,
        response_examples=RESPONSE_EXAMPLES,
        interpret_examples=INTERPRET_EXAMPLES,
    )


def _eeyore_birthday() -> Scenario:
    from eeyore_birthday_events import (
        EeyoreAnalyzeInteraction,
        EeyoreGenerateResponse,
        EeyoreInterpretTurn,
        HoneyGiftEventController,
    )
    from narrative_events import estimate_speech_seconds
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
            SceneDefinition("5", "場面5：贈り物の詳細を相談する"),
            SceneDefinition("6", "場面6：贈り物を振り返る・会話を続ける"),
        ),
        event_controller_factory=lambda clock: HoneyGiftEventController(
            EEYORE_GIFT_DECISION_DELAY_SECONDS,
            EEYORE_HONEY_TASTING_DELAY_SECONDS,
            EEYORE_HONEY_EATING_DELAY_SECONDS,
            clock=clock,
            quiet_seconds=EEYORE_EVENT_QUIET_SECONDS,
            idle_close_seconds=EEYORE_IDLE_CLOSE_AFTER_WRAP_UP_SECONDS,
            speech_seconds=estimate_speech_seconds,
            awaiting_quiet_seconds=EEYORE_AWAITING_REPLY_QUIET_SECONDS,
            ask_empty_jar_delay_seconds=EEYORE_ASK_EMPTY_JAR_DELAY_SECONDS,
        ),
        pooh_preferences=EEYORE_BIRTHDAY_POOH_PREFERENCES,
        # 「プー、今日はイーヨーについて相談したいことがあるんだよね」;
        # the recognizer also hears イーヨー as イオン, いいよう or いーよ.
        start_cues=(("イーヨ", "相談"), ("イオン", "相談"), ("いいよう", "相談"),
                    ("いーよ", "相談")),
        classify_signature=EeyoreAnalyzeInteraction,
        interpret_signature=EeyoreInterpretTurn,
        response_signature=EeyoreGenerateResponse,
    )


def _footprints() -> Scenario:
    from footprints_events import (
        FootprintsAnalyzeInteraction,
        FootprintsEventController,
        FootprintsGenerateResponse,
        FootprintsInterpretTurn,
    )
    from narrative_events import estimate_speech_seconds
    from pooh_examples import MISHEARING_EXAMPLES
    from pooh_footprints_examples import (
        FOOTPRINTS_INTERPRET_EXAMPLES,
        FOOTPRINTS_MODE_EXAMPLES,
        FOOTPRINTS_OPENING_LINE,
        FOOTPRINTS_POOH_PREFERENCES,
        FOOTPRINTS_RESPONSE_EXAMPLES,
        FOOTPRINTS_SITUATION,
        FOOTPRINTS_TRAINSET,
    )

    return Scenario(
        key="footprints",
        label="あしあとの正体",
        scene_intro=(
            "雪が降ったあとの100エーカーの森。小さな茂みのまわりに、"
            "見慣れない足跡が残っています。プーがそれをのぞきこんでいます。"
        ),
        initial_situation=FOOTPRINTS_SITUATION,
        opening_line=FOOTPRINTS_OPENING_LINE,
        ending_line=fixed_utterance("footprints.ending"),
        trainset=FOOTPRINTS_TRAINSET,
        mode_examples=FOOTPRINTS_MODE_EXAMPLES,
        mishearing_examples=MISHEARING_EXAMPLES,
        response_examples=FOOTPRINTS_RESPONSE_EXAMPLES,
        interpret_examples=FOOTPRINTS_INTERPRET_EXAMPLES,
        scenes=(
            SceneDefinition("1", "場面1：足跡を発見する"),
            SceneDefinition("2", "場面2：正体に名前をつける（参加者の案、またはプー自身の案）"),
            SceneDefinition("3", "場面3：正体の見た目を参加者と相談する"),
            SceneDefinition("4", "場面4：足跡を追いかけ始める"),
            SceneDefinition("5", "場面5：足跡が２匹分になっていることに気づく"),
            SceneDefinition("6", "場面6：足跡が４匹分に増えたことに気づく"),
            SceneDefinition("7a", "場面7a：プーが気づきを開いた形で促す"),
            SceneDefinition("7b", "場面7b：プーが具体的なヒントを出す"),
            SceneDefinition("7c", "場面7c：プーがより直接的な推測を示す"),
            SceneDefinition("8", "場面8：自分たちの足跡だったと判明し、会話を続ける"),
        ),
        event_controller_factory=lambda clock: FootprintsEventController(
            FOOTPRINTS_ASK_NAME_DELAY_SECONDS,
            FOOTPRINTS_NAMING_DELAY_SECONDS,
            FOOTPRINTS_ASK_APPEARANCE_DELAY_SECONDS,
            FOOTPRINTS_START_FOLLOWING_DELAY_SECONDS,
            FOOTPRINTS_SECOND_ANIMAL_DELAY_SECONDS,
            FOOTPRINTS_FOURTH_ANIMAL_DELAY_SECONDS,
            FOOTPRINTS_REVEAL_STEP_DELAY_SECONDS,
            clock=clock,
            quiet_seconds=FOOTPRINTS_EVENT_QUIET_SECONDS,
            idle_close_seconds=FOOTPRINTS_IDLE_CLOSE_AFTER_REVEAL_SECONDS,
            speech_seconds=estimate_speech_seconds,
            awaiting_quiet_seconds=FOOTPRINTS_AWAITING_REPLY_QUIET_SECONDS,
        ),
        pooh_preferences=FOOTPRINTS_POOH_PREFERENCES,
        # 「プー，さっき何かを見つけたって言っていたよね．どうしたの？」
        start_cues=(("見つけた", "どうした"),),
        classify_signature=FootprintsAnalyzeInteraction,
        interpret_signature=FootprintsInterpretTurn,
        response_signature=FootprintsGenerateResponse,
    )


SCENARIOS: dict[str, Scenario] = {
    "tea_party": _tea_party(),
    "eeyore_birthday": _eeyore_birthday(),
    "footprints": _footprints(),
}

DEFAULT_SCENARIO = "tea_party"


def selected_scenario_keys(primary: str, extras: list[str]) -> list[str]:
    """Keep the default first; expand all from the registry and deduplicate."""
    all_keys = sorted(SCENARIOS, key=lambda key: (key != "eeyore_birthday", key))
    keys = list(all_keys) if primary == "all" else [primary]
    for key in extras:
        keys.extend(all_keys if key == "all" else [key])
    for key in keys:
        get_scenario(key)  # Validate explicit names, including the primary.
    return list(dict.fromkeys(keys))


def get_scenario(key: str) -> Scenario:
    try:
        return SCENARIOS[key]
    except KeyError as exc:
        available = "、".join(sorted(SCENARIOS))
        raise ValueError(f"未知のシナリオです: {key}（利用可能: {available}）") from exc
