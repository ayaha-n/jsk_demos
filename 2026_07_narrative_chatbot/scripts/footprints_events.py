"""The footprints scenario's own timed narrative events.

Unlike the first draft of this scenario (fully learned response generation),
the required beats here — noticing the tracks look like two animals, then
four, and the three-step nudge toward realizing they are the pair's own
footprints — are each a ``RequiredNarrativeEvent`` with a fixed line, exactly
like eeyore_birthday_events.py's honey-jar beats. A compiled model's learned
behavior turned out not to reproduce these specific lines reliably from a
small trainset; a Python-owned clock does.

Signaling an organic equivalent of a Python-fired beat follows the same split
as eeyore_birthday_events: a closed ``narrative_actions`` vocabulary (here
``FootprintsAction``) for plain "did X happen" occurrences — started
following, noticed the count rise, realized the footprints are the pair's
own — so that beat's later timed fire is pre-empted instead of duplicated;
and the open, free-text ``settled_details`` mechanism (see
narrative_events.SettledDetail) for naming and for appearance, where the
actual chosen text (not just that the topic was settled) matters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

import dspy

from mishearing_cases import MishearingCandidate
from narrative_events import (
    NarrativeEventController,
    RequiredNarrativeEvent,
    SettledDetail,
    WorldEvent,
    fixed_utterance,
)
from narrative_signatures import (
    AnalyzeInteraction,
    GeneratePoohResponse,
    InterpretTurn,
    QuestionKind,
    _signature_field,
)
from narrative_state import NarrativeSituation, SituationUpdate, apply_situation_update


FootprintsAction = Literal[
    "started_following",
    "noticed_second_animal",
    "noticed_fourth_animal",
    "realized_own_footprints",
    "pooh_suggested_own_footprints",
    "gave_hint_directly",
]
KNOWN_FOOTPRINTS_ACTIONS = {
    "started_following",
    "noticed_second_animal",
    "noticed_fourth_animal",
    "realized_own_footprints",
    "pooh_suggested_own_footprints",
    "gave_hint_directly",
}

# A participant who explicitly gives up after reveal_prompt has already
# asked "何か気づかない？" gets the hint right away instead of another vague
# deflection (see FootprintsEventController.fixed_line_for). Judging "gave up"
# from the participant's own words is exactly what an LM is for — a fixed
# keyword list (e.g. matching "わからない") is both brittle (misses "お手上げ",
# "思いつかない", ...) and too eager (would also fire mid-sentence on
# unrelated uses) — so this is read from AnalyzeInteraction's classify stage,
# the same way eeyore_birthday_events.EeyoreAnalyzeInteraction's
# honey_requests field feeds HoneyGiftEventController.fixed_line_for.
class FootprintsAnalyzeInteraction(AnalyzeInteraction):
    gave_up_on_noticing: bool = dspy.OutputField(
        desc=(
            "プーが「なにか気づかない？」のように気づきを促した直後の参加者の発話が"
            "対象。参加者が自分の言葉で、これは自分たちの足跡なのではないかと"
            "明確に言い当てた場合だけfalse。"
            "「わからない」「思いつかない」「お手上げ」のようにはっきり分からないと"
            "表明した場合はもちろん、「なんのこと？」「え、なんだろう」のような、"
            "まだ気づけていない曖昧な聞き返し・反応も含めてtrue"
            "（もう一度ぼんやり促しても堂々巡りになるだけなので、ヒントを出す）。"
            "そのような問いかけが直前になければfalse。"
        )
    )
    # Whether the participant themselves has floated the twist this turn.
    # Read from the participant's words alone, before Pooh's line exists:
    # interpret() judges the turn together with Pooh's line, so a line that
    # leaked the twist on its own was once enough for it to report
    # realized_own_footprints (confirmed live; see docs/known_issues.md).
    participant_suggested_own_footprints: bool = dspy.OutputField(
        desc=(
            "参加者がこのターンの自分の発話で、この足跡はプーと参加者自身の足跡"
            "なのではないか（自分たちが歩いてつけた・増やした、など）と言った・"
            "疑問として持ち出した場合だけtrue。"
            "プーが直前に「このあしあとはぼくのあし、こっちはきみのあしと同じ大きさ」"
            "のような手がかりを出し、参加者がそれに「ほんとだ」「そうかも」のように"
            "同意・納得した場合もtrue。"
            "「誰の足跡だろう」「増えたね」「足跡をたどろう」のように、足跡の話を"
            "しているだけで自分たちの足跡だとは言っていない場合はfalse。"
            "「気づいた！」「わかった！」のように、気づいたと言うだけで何に気づいたかを"
            "言っていない場合もfalse（中身を言うまでは言い当てたことにならない）。"
        )
    )
    # A participant who answers "何か気づかない？" with only "気づいた！"
    # has not said what; gpt-4o read exactly that as having said the tracks
    # were their own (3 of 3 times on logs/session_20261106T210454...), and
    # Pooh then gave the answer away himself. Python asks what, instead (see
    # FootprintsEventController.fixed_line_for).
    vague_noticing: bool = dspy.OutputField(
        desc=(
            "プーが「なにか気づかない？」と気づきを促したあとの参加者の発話が、"
            "「気づいた！」「わかった！」「あっ、そういうことか」のように、"
            "気づいた・わかったと言うだけで、何に気づいたか（足跡がだれのものか）を"
            "言っていない場合だけtrue。何に気づいたかを言っている場合、"
            "「わからない」と言っている場合、そのような問いかけが直前にない場合はfalse。"
        )
    )
    proposed_name: str = dspy.OutputField(
        desc=(
            "現在のunresolvedに「正体の呼び名」が含まれている場面で、参加者が"
            "このターンの発話で、足跡の主を呼ぶためのニックネームとして具体的に"
            "提案した一語の名前。「〜って呼ぼう」「〜っていう名前にしよう」"
            "「〜って呼んだらどうかな」のように、呼び方そのものを提案した場合"
            "だけが対象。"
            "「うさぎみたいな動物かな」「ゆきうさぎとかかな」のように、正体が"
            "何の生き物かを推測しているだけの発話は対象外（呼び名の提案ではなく、"
            "「この足跡は何の生き物のものか」という別の未解決項目についての"
            "推測にすぎない）。"
            "「ティガーとか？」「ピグレットじゃない？」のように、プーが既に知って"
            "いる実在の友だちの名前を挙げた場合も対象外（新しいニックネームの提案"
            "ではなく、その友だち本人なのではという推測であり、呼び名の提案と"
            "同じには扱えない）。"
            "まだ名付けられていない場合だけが対象。"
            "該当しなければ空文字列。"
        )
    )

# settled_details topic marking that Pooh has named the creature (see
# pooh_footprints_examples.py's 場面2); arms the start_following clock.
NAMING_TOPIC = "正体の呼び名"
# The scenario's main unresolved label; settling it (by the participant's own
# realization, or by _fire_reveal_direct) stops any further escalation.
IDENTITY_UNRESOLVED = "この足跡は何の生き物のものか"
# Raised once naming is decided (organically or by _fire_name_creature);
# resolved during 場面3's appearance discussion.
APPEARANCE_UNRESOLVED = "正体の見た目はどんな様子か"


# InterpretTurn, extended with footprints' own closed vocabulary — built the
# same way as eeyore_birthday_events.EeyoreInterpretTurn, for the same reason
# (field order; see that class's comment).
_FOOTPRINTS_NARRATIVE_ACTIONS_FIELD = dspy.OutputField(
    desc=(
        "Pythonが検証する機械可読な報告。必要なものだけを返す。利用可能: "
        "started_following(名前と見た目の相談を済ませ、プーと参加者が実際に追跡を始めた場合。導入の説明や、探しに行こうという誘いだけは含めない)、"
        "noticed_second_animal(足跡が２匹分に見えることに気づいた場合)、"
        "noticed_fourth_animal(足跡がさらに増えて４匹分になったことに気づいた場合。"
        "２匹分の段階を経ていなくてもよい)、"
        "realized_own_footprints(自分たちの足跡だったと、参加者が自分の言葉で"
        "明確に言い当てた場合のみ。「え，なんだろう」のような曖昧な返答・"
        "聞き返しだけでは判定しない。プーの応答だけがそう言っている場合は"
        "対象外)、"
        "pooh_suggested_own_footprints(プーの応答が、足跡はプーと参加者自身の"
        "ものだ、またはそうかもしれないと言った・ほのめかした場合。参加者が"
        "言ったかどうかに関係なく、プーの応答だけを見て判定する)。"
        "該当しなければ空リスト。"
    )
)
FootprintsInterpretTurn = type(
    "FootprintsInterpretTurn",
    (dspy.Signature,),
    {
        "__doc__": getattr(InterpretTurn, "instructions", InterpretTurn.__doc__),
        "__annotations__": {
            "current_situation": NarrativeSituation,
            "history": str,
            "user_action": str,
            "world_event": str,
            "bot_response": str,
            "situation_update": SituationUpdate,
            "narrative_actions": list[FootprintsAction],
            "settled_details": list[SettledDetail],
            "question_kind": QuestionKind,
        },
        "current_situation": _signature_field(InterpretTurn, "current_situation"),
        "history": _signature_field(InterpretTurn, "history"),
        "user_action": _signature_field(InterpretTurn, "user_action"),
        "world_event": _signature_field(InterpretTurn, "world_event"),
        "bot_response": _signature_field(InterpretTurn, "bot_response"),
        "situation_update": _signature_field(InterpretTurn, "situation_update"),
        "narrative_actions": _FOOTPRINTS_NARRATIVE_ACTIONS_FIELD,
        "settled_details": _signature_field(InterpretTurn, "settled_details"),
        "question_kind": _signature_field(InterpretTurn, "question_kind"),
    },
)


# GeneratePoohResponse, extended with footprints' own constraint: Pooh must
# never float the actual twist (that the tracks are the pair's own) himself,
# except through reveal_direct's scripted line. GeneratePoohResponse's shared
# instructions already tell Pooh to volunteer his own idea about whatever is
# still unresolved once a topic winds down — for footprints, IDENTITY_UNRESOLVED
# IS the twist, so without this carve-out Pooh ends up guessing it himself in
# an aside, and the story then carries on as if nothing had been said (see
# docs/known_issues.md). Built with type() (not a plain docstring subclass)
# so the base instructions are kept, not replaced — same reasoning as
# EeyoreAnalyzeInteraction above, just appending text instead of adding a
# field this time.
_FOOTPRINTS_RESPONSE_EXTRA_INSTRUCTIONS = (
    f"未解決の項目のうち「{IDENTITY_UNRESOLVED}」だけは特別で、他の項目（見た目など）とは"
    "違う扱いをする。プー自身が「これは自分たちの足あとかもしれない」のように正体そのものを"
    "示唆することは、reveal_directの場面（参加者がヒントを重ねても気づかない場合の最後の"
    "推測）以外では、参加者が迷ったり「わからない」と答えたりした場合も含めて一切しない。"
    "この項目に話を戻すときも、正体の推測ではなく、追いかける・観察するなど周辺の行動や"
    "感想にとどめる。"
    "足あとを追いかける話をするときは、「足あとをたどっていけば会えるかも」のように、"
    "足あとをたどることと、その主に会えることのつながりが分かる言い方にする。"
    "「なにか気づかない？」と尋ねた後に参加者が「なんのこと？」「わからない」のように"
    "聞き返した・答えなかった場合も、具体的な手がかり（足あとの大きさがぼくたちと同じ、"
    "など）や推測を自分から明かさない。もう一度ぼんやりと促す、または「気のせいかもしれ"
    "ない」のようにはぐらかすだけに留める。具体的な手がかりと推測はreveal_hint・"
    "reveal_directの固定セリフだけが出す。"
    "ただし、プー自身が「なにか気づかない？」と問いかけた場面であり、プーの方から話を"
    "切り出している以上、「ぼくもわからない」「ぼくも気づいていない」のように自分も"
    "何も気づいていないかのように答えるのは不自然であり、しない。プーは何か気になる"
    "ことがある、という態度は保ったまま、具体的な中身だけをぼかす。"
)
FootprintsGenerateResponse = type(
    "FootprintsGenerateResponse",
    (dspy.Signature,),
    {
        "__doc__": (
            getattr(GeneratePoohResponse, "instructions", GeneratePoohResponse.__doc__ or "")
            + "\n\n"
            + _FOOTPRINTS_RESPONSE_EXTRA_INSTRUCTIONS
        ),
        "__annotations__": {
            "current_situation": NarrativeSituation,
            "history": str,
            "user_action": str,
            "world_event": str,
            "previous_bot_response": str,
            "pooh_preferences": str,
            "interaction_mode": str,
            "mishearing_candidates": list[MishearingCandidate],
            "selected_mishearing": str,
            "bot_response": str,
        },
        "current_situation": _signature_field(GeneratePoohResponse, "current_situation"),
        "history": _signature_field(GeneratePoohResponse, "history"),
        "user_action": _signature_field(GeneratePoohResponse, "user_action"),
        "world_event": _signature_field(GeneratePoohResponse, "world_event"),
        "previous_bot_response": _signature_field(GeneratePoohResponse, "previous_bot_response"),
        "pooh_preferences": _signature_field(GeneratePoohResponse, "pooh_preferences"),
        "interaction_mode": _signature_field(GeneratePoohResponse, "interaction_mode"),
        "mishearing_candidates": _signature_field(GeneratePoohResponse, "mishearing_candidates"),
        "selected_mishearing": _signature_field(GeneratePoohResponse, "selected_mishearing"),
        "bot_response": _signature_field(GeneratePoohResponse, "bot_response"),
    },
)

ASK_NAME_RESPONSE = fixed_utterance("footprints.ask_name")
ASK_APPEARANCE_RESPONSE = fixed_utterance("footprints.ask_appearance")
NAMING_EVENT = "プーが足跡の主に「ウズウズ」と名付けた"
NAMING_RESPONSE = fixed_utterance("footprints.naming")
START_FOLLOWING_EVENT = "プーと参加者がウズウズの足跡を追いかけ始めた"
START_FOLLOWING_RESPONSE = fixed_utterance("footprints.start_following")
SECOND_ANIMAL_EVENT = "足跡が２匹分になっていることに気づいた"
SECOND_ANIMAL_RESPONSE = fixed_utterance("footprints.second_animal")
FOURTH_ANIMAL_EVENT = "足跡が４匹分に増えていることに気づいた"
FOURTH_ANIMAL_RESPONSE = fixed_utterance("footprints.fourth_animal")
REVEAL_PROMPT_RESPONSE = fixed_utterance("footprints.reveal_prompt")
REVEAL_HINT_RESPONSE = fixed_utterance("footprints.reveal_hint")
ASK_WHAT_NOTICED_RESPONSE = fixed_utterance("footprints.ask_what_noticed")
REVEAL_DIRECT_EVENT = "実はウズウズの足跡は自分たちの足跡だったとわかった"
REVEAL_DIRECT_RESPONSE = fixed_utterance("footprints.reveal_direct")

# A generated line that states or hints the twist (that the tracks are the
# pair's own) before the participant raised it and before reveal_direct is
# replaced (see FootprintsEventController.enforce_response_invariants). The
# response instructions already forbid this, but gpt-4o still did it live,
# unprompted, right after fourth_animal ("足跡が僕たちのだったなんて"). Whether
# a line does is judged by interpret() (pooh_suggested_own_footprints), not by
# a keyword list, which would miss rephrasings.
# What Pooh says instead: stays with following the tracks, the activity the
# hint cascade expects to still be under way.
TWIST_LEAK_REPLACEMENT = "ふしぎだなあ。もうすこし、足あとをたどってみようよ。"


@dataclass
class FootprintsState:
    # The name actually used, whether Pooh invented it himself (organically
    # or via _fire_name_creature's "ウズウズ") or the participant proposed
    # it; settled_details' value is never interpreted, only kept as-is.
    name: str | None = None
    named: bool = False
    following_started: bool = False
    # Settled by the participant's own description, or by Pooh's own guess,
    # either before or after ask_appearance fires; see observe_settled_details.
    appearance_settled: bool = False
    # Settled early (the participant's own realization) or by _fire_reveal_direct.
    resolved: bool = False
    # Pooh has asked "なにに気づいたの？" after a bare "気づいた！" once.
    asked_what_noticed: bool = False
    completed_event_ids: set[str] = field(default_factory=set)


class FootprintsEventController(NarrativeEventController):
    """Own the clock for the footprint-count escalation and reveal cascade."""

    def __init__(
        self,
        ask_name_delay_seconds: float,
        naming_delay_seconds: float,
        ask_appearance_delay_seconds: float,
        start_following_delay_seconds: float,
        second_animal_delay_seconds: float,
        fourth_animal_delay_seconds: float,
        reveal_step_delay_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        required_events: tuple[RequiredNarrativeEvent, ...] | None = None,
        quiet_seconds: float = 0.0,
        idle_close_seconds: float | None = None,
        speech_seconds: Callable[[str], float] = lambda text: 0.0,
        awaiting_quiet_seconds: float | None = None,
    ) -> None:
        if ask_name_delay_seconds < 0:
            raise ValueError("ask_name_delay_seconds must be non-negative")
        if naming_delay_seconds < 0:
            raise ValueError("naming_delay_seconds must be non-negative")
        if ask_appearance_delay_seconds < 0:
            raise ValueError("ask_appearance_delay_seconds must be non-negative")
        if start_following_delay_seconds < 0:
            raise ValueError("start_following_delay_seconds must be non-negative")
        if second_animal_delay_seconds < 0:
            raise ValueError("second_animal_delay_seconds must be non-negative")
        if fourth_animal_delay_seconds < 0:
            raise ValueError("fourth_animal_delay_seconds must be non-negative")
        if reveal_step_delay_seconds < 0:
            raise ValueError("reveal_step_delay_seconds must be non-negative")
        if idle_close_seconds is not None and idle_close_seconds < 0:
            raise ValueError("idle_close_seconds must be non-negative")
        self.ask_name_delay_seconds = ask_name_delay_seconds
        self.naming_delay_seconds = naming_delay_seconds
        self.ask_appearance_delay_seconds = ask_appearance_delay_seconds
        self.start_following_delay_seconds = start_following_delay_seconds
        self.second_animal_delay_seconds = second_animal_delay_seconds
        self.fourth_animal_delay_seconds = fourth_animal_delay_seconds
        self.reveal_step_delay_seconds = reveal_step_delay_seconds
        # Set per participant turn by fixed_line_for; see there.
        self._participant_suggested_own_footprints = False
        super().__init__(
            state=FootprintsState(),
            required_events=required_events or self._default_required_events(
                ask_name_delay_seconds,
                naming_delay_seconds,
                ask_appearance_delay_seconds,
                start_following_delay_seconds,
                second_animal_delay_seconds,
                fourth_animal_delay_seconds,
                reveal_step_delay_seconds,
                idle_close_seconds,
            ),
            clock=clock,
            quiet_seconds=quiet_seconds,
            speech_seconds=speech_seconds,
            awaiting_quiet_seconds=awaiting_quiet_seconds,
        )

    @staticmethod
    def _default_required_events(
        ask_name_delay_seconds: float,
        naming_delay_seconds: float,
        ask_appearance_delay_seconds: float,
        start_following_delay_seconds: float,
        second_animal_delay_seconds: float,
        fourth_animal_delay_seconds: float,
        reveal_step_delay_seconds: float,
        idle_close_seconds: float | None = None,
    ) -> tuple[RequiredNarrativeEvent, ...]:
        events = (
            # Asking for a name and asking about appearance are fixed lines,
            # the same follow_up_response idea eeyore_birthday_events uses
            # (e.g. honey_gift_committed's "あ、そういえば。…"): generating
            # these organically turned out to collapse into the same turn as
            # the answer far too often, so Python asks instead of hoping the
            # model phrases a clean, separate question.
            RequiredNarrativeEvent(
                event_id="ask_name",
                delay_seconds=ask_name_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    not state.named and not state.resolved
                ),
                fire=FootprintsEventController._fire_ask_name,
            ),
            # Deciding for himself if nobody answers ask_name in time; free to
            # happen organically too (see pooh_footprints_examples.py's 場面2).
            RequiredNarrativeEvent(
                event_id="name_creature",
                delay_seconds=naming_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    not state.named and not state.resolved
                    and "ask_name" in state.completed_event_ids
                ),
                fire=FootprintsEventController._fire_name_creature,
            ),
            RequiredNarrativeEvent(
                event_id="ask_appearance",
                delay_seconds=ask_appearance_delay_seconds,
                waits_for_quiet=True,
                # A participant who asks what the creature looks like (or
                # Pooh, prompted by that) before this ever fires has already
                # covered what this line would ask; re-asking afterward was
                # confirmed live as a redundant repeat.
                prerequisite=lambda state: (
                    state.named and not state.appearance_settled
                    and not state.following_started and not state.resolved
                ),
                fire=FootprintsEventController._fire_ask_appearance,
            ),
            # Give appearance its own response window. Naming alone must not
            # arm this timer in parallel with ask_appearance. An organic
            # appearance discussion can replace that fixed question.
            RequiredNarrativeEvent(
                event_id="start_following",
                delay_seconds=start_following_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    state.named and not state.following_started and not state.resolved
                    and ("ask_appearance" in state.completed_event_ids or state.appearance_settled)
                ),
                fire=FootprintsEventController._fire_start_following,
            ),
            RequiredNarrativeEvent(
                event_id="second_animal",
                delay_seconds=second_animal_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: state.following_started and not state.resolved,
                fire=FootprintsEventController._fire_second_animal,
            ),
            RequiredNarrativeEvent(
                event_id="fourth_animal",
                delay_seconds=fourth_animal_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    "second_animal" in state.completed_event_ids and not state.resolved
                ),
                fire=FootprintsEventController._fire_fourth_animal,
            ),
            # The three-step nudge toward realizing the tracks are their own.
            # Each step only fires if the participant has not already
            # resolved it themselves (checked the same way by every step).
            RequiredNarrativeEvent(
                event_id="reveal_prompt",
                delay_seconds=reveal_step_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    "fourth_animal" in state.completed_event_ids and not state.resolved
                ),
                fire=FootprintsEventController._fire_reveal_prompt,
            ),
            RequiredNarrativeEvent(
                event_id="reveal_hint",
                delay_seconds=reveal_step_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    "reveal_prompt" in state.completed_event_ids and not state.resolved
                ),
                fire=FootprintsEventController._fire_reveal_hint,
            ),
            RequiredNarrativeEvent(
                event_id="reveal_direct",
                delay_seconds=reveal_step_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    "reveal_hint" in state.completed_event_ids and not state.resolved
                ),
                fire=FootprintsEventController._fire_reveal_direct,
            ),
        )
        if idle_close_seconds is None:
            return events
        return events + (
            RequiredNarrativeEvent(
                event_id="idle_close_after_reveal",
                delay_seconds=0.0,
                waits_for_quiet=True,
                prerequisite=lambda state: state.resolved,
                fire=FootprintsEventController._fire_idle_close,
                quiet_seconds=idle_close_seconds,
                # A participant who keeps talking wants to continue.
                follow_up_allowed=False,
            ),
        )

    def observe_actions(self, actions: list[str]) -> None:
        """Apply LM-reported narrative actions, the same closed-vocabulary
        channel eeyore_birthday_events.HoneyGiftEventController uses: an
        organic equivalent of a Python-fired beat pre-empts its later timed
        fire instead of duplicating it once the model does get around to it."""
        for action in actions:
            if action not in KNOWN_FOOTPRINTS_ACTIONS:
                continue
            if action == "started_following":
                if not self.state.named:
                    # An invitation in the introduction is not the tracking
                    # beat. Ask for a name first instead of forcing a name.
                    self._deadlines["ask_name"] = self.clock()
                    continue
                if not (self.state.appearance_settled or "ask_appearance" in self.state.completed_event_ids):
                    self._deadlines["ask_appearance"] = self.clock()
                    continue
                self.state.following_started = True
            elif action == "noticed_second_animal":
                if not self.state.following_started:
                    continue
                self.state.completed_event_ids.add("second_animal")
            elif action == "noticed_fourth_animal":
                if not self.state.following_started:
                    continue
                self.state.completed_event_ids.add("second_animal")
                self.state.completed_event_ids.add("fourth_animal")
            elif action == "realized_own_footprints":
                # Only the participant's own words count (see
                # FootprintsAnalyzeInteraction.participant_suggested_own_footprints);
                # a world-event turn has no participant words at all.
                if self._participant_suggested_own_footprints:
                    self.state.resolved = True
            elif action == "gave_hint_directly":
                self.state.completed_event_ids.add("reveal_hint")
        self._participant_suggested_own_footprints = False
        self._arm_required_events()

    def observe_settled_details(self, details: list[SettledDetail | dict]) -> None:
        """Record the chosen name, whoever picked it; arms start_following.
        Also records the appearance once settled, by the participant or by
        Pooh's own guess, so ask_appearance never re-asks once it already
        has an answer (see that event's prerequisite)."""
        for detail in details:
            detail = SettledDetail.model_validate(detail)
            if detail.topic == NAMING_TOPIC:
                self.state.named = True
                self.state.name = detail.value
            elif detail.topic == APPEARANCE_UNRESOLVED:
                self.state.appearance_settled = True
        self._arm_required_events()

    @staticmethod
    def _fire_ask_name(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("ask_name")
        return WorldEvent(
            event_id="ask_name",
            description="プーが参加者に、名前を考えていると伝えた。これは既に起きた出来事。",
            scene_id="1",
            fallback_response=ASK_NAME_RESPONSE,
            # Only now does naming become a visible unresolved topic (and so
            # only now does pooh_preferences start surfacing Pooh's own
            # "「ウズウズ」って呼びたいな。" as an option) — listing it as
            # unresolved from session start let the model volunteer the name
            # before this question was ever actually asked.
            situation_update=SituationUpdate(add_unresolved=[NAMING_TOPIC]),
            fixed_response=True,
        )

    @staticmethod
    def _fire_ask_appearance(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("ask_appearance")
        # The registered fixed line (with its recorded wav/motion) says
        # "ウズウズ" — Python's own default name. A participant-chosen name
        # falls back to a line built with the actual name instead; it still
        # fires deterministically, just without a matching wav/motion (same
        # as any other line fixed_utterance_id() cannot match).
        line = (
            ASK_APPEARANCE_RESPONSE
            if state.name == "ウズウズ"
            else f"ところで、{state.name}の見た目はどんな感じだと思う？"
        )
        return WorldEvent(
            event_id="ask_appearance",
            description="プーが参加者に、見た目について尋ねた。これは既に起きた出来事。",
            scene_id="3",
            fallback_response=line,
            situation_update=SituationUpdate(),
            fixed_response=True,
        )

    @staticmethod
    def _fire_name_creature(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("name_creature")
        state.named = True
        state.name = "ウズウズ"
        return WorldEvent(
            event_id="name_creature",
            description="プーが足跡の主に「ウズウズ」と名付けた。これは既に起きた出来事。",
            scene_id="2",
            fallback_response=NAMING_RESPONSE,
            situation_update=SituationUpdate(
                add_events=[NAMING_EVENT],
                remove_unresolved=[NAMING_TOPIC],
                add_unresolved=[APPEARANCE_UNRESOLVED],
            ),
            fixed_response=True,
        )

    @staticmethod
    def _fire_start_following(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("start_following")
        state.following_started = True
        # Same hybrid as _fire_ask_appearance: the registered line (with its
        # recorded wav/motion) says "ウズウズ" for Python's own default name;
        # a participant-chosen name falls back to a dynamically built line.
        line = (
            START_FOLLOWING_RESPONSE
            if state.name == "ウズウズ"
            else (
                f"この足あとをたどっていけば、{state.name}に会えるかもしれないね。"
                "よし、追いかけてみよう。きみも一緒に来てくれない？"
            )
        )
        return WorldEvent(
            event_id="start_following",
            description=(
                "プーが参加者を誘って、ウズウズの足跡を追いかけ始めた。"
                "これは既に起きた出来事。"
            ),
            scene_id="4",
            fallback_response=line,
            situation_update=SituationUpdate(add_events=[START_FOLLOWING_EVENT]),
            fixed_response=True,
        )

    @staticmethod
    def _fire_second_animal(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("second_animal")
        return WorldEvent(
            event_id="second_animal",
            description=(
                "足跡をたどっていくうちに、動物が２匹いるように見えることに"
                "気づいた。これは既に起きた出来事。"
            ),
            scene_id="5",
            fallback_response=SECOND_ANIMAL_RESPONSE,
            situation_update=SituationUpdate(
                remove_props=["足あと（２つ）"],
                add_props=["足あと（４つ）"],
                add_events=[SECOND_ANIMAL_EVENT],
            ),
            fixed_response=True,
        )

    @staticmethod
    def _fire_fourth_animal(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("fourth_animal")
        return WorldEvent(
            event_id="fourth_animal",
            description=(
                "足跡がさらに増え、４匹分になっていることに気づいた。"
                "これは既に起きた出来事。"
            ),
            scene_id="6",
            fallback_response=FOURTH_ANIMAL_RESPONSE,
            situation_update=SituationUpdate(
                remove_props=["足あと（４つ）"],
                add_props=["足あと（８つ）"],
                add_events=[FOURTH_ANIMAL_EVENT],
            ),
            fixed_response=True,
        )

    @staticmethod
    def _fire_reveal_prompt(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("reveal_prompt")
        return WorldEvent(
            event_id="reveal_prompt",
            description="プーが参加者に、何か気づかないかと問いかけた。これは既に起きた出来事。",
            scene_id="7a",
            fallback_response=REVEAL_PROMPT_RESPONSE,
            situation_update=SituationUpdate(),
            fixed_response=True,
        )

    @staticmethod
    def _fire_reveal_hint(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("reveal_hint")
        return WorldEvent(
            event_id="reveal_hint",
            description=(
                "プーが、足跡の大きさが自分たちの足と同じだと指摘した。"
                "これは既に起きた出来事。"
            ),
            scene_id="7b",
            fallback_response=REVEAL_HINT_RESPONSE,
            situation_update=SituationUpdate(),
            fixed_response=True,
        )

    @staticmethod
    def _fire_reveal_direct(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("reveal_direct")
        state.resolved = True
        return WorldEvent(
            event_id="reveal_direct",
            description=(
                "プーが、これは自分たちの足跡なのではないかと言った。"
                "これは既に起きた出来事。"
            ),
            scene_id="7c",
            fallback_response=REVEAL_DIRECT_RESPONSE,
            situation_update=SituationUpdate(
                remove_unresolved=[IDENTITY_UNRESOLVED],
                add_events=[REVEAL_DIRECT_EVENT],
            ),
            fixed_response=True,
        )

    def fixed_line_for(self, classification: Any) -> str | None:
        """A fixed line for this turn's classify-stage result, for the
        cases generation proved unreliable at handling on its own.

        proposed_name (FootprintsAnalyzeInteraction.proposed_name): once the
        participant names the creature themselves, accepting it outright as a
        fixed line (and settling it here, as a side effect, since a
        fixed_line_for turn skips interpret() entirely — see
        synchronize_situation) sidesteps a real generation bug confirmed
        live: with a longer preceding history, the model would bring up
        Pooh's own naming preference (pooh_preferences) alongside the
        participant's name instead of simply accepting it, even though both
        the instructions and a directly matching trainset example already
        said not to — more matching trainset examples did not fix this.

        gave_up_on_noticing: once reveal_prompt has asked "何か気づかない？",
        repeating a vague deflection every time the participant says they do
        not know reads as Pooh stonewalling rather than noticing something
        himself (confirmed live). The fixed reveal_hint line is the right
        amount of help here, reused verbatim instead of generated, so it
        never duplicates what the timed reveal_hint event would otherwise
        still say later (see extra_actions_for, which marks reveal_hint done
        once this fires).

        vague_noticing: after "何か気づかない？", a bare "気づいた！" is
        answered with "なにに気づいたの？" (once; a second bare one gets the
        hint), never with the answer itself (confirmed live: Pooh said "その
        足あとは、ぼくたちのだったんだね" to it). The timed hint and reveal
        go on as usual if no answer comes.
        """
        # Turn-scoped: read by enforce_response_invariants and observe_actions
        # later in this same turn, then cleared by observe_actions.
        self._participant_suggested_own_footprints = bool(
            getattr(classification, "participant_suggested_own_footprints", False)
        )
        noticing = (
            "reveal_prompt" in self.state.completed_event_ids and not self.state.resolved
        )
        if noticing and getattr(classification, "vague_noticing", False):
            # 「気づいた！」 alone has not said what: not a realization, so
            # Pooh may not agree with an answer nobody gave. He asks what;
            # asked once already, he gives the hint instead of asking again.
            self._participant_suggested_own_footprints = False
            if not self.state.asked_what_noticed:
                self.state.asked_what_noticed = True
                return ASK_WHAT_NOTICED_RESPONSE
            if "reveal_hint" not in self.state.completed_event_ids:
                return REVEAL_HINT_RESPONSE
        proposed_name = str(getattr(classification, "proposed_name", "") or "").strip()
        if proposed_name and not self.state.named:
            self.state.named = True
            self.state.name = proposed_name
            return f"{proposed_name}か、いいね！そう呼ぼう。"
        if (
            getattr(classification, "gave_up_on_noticing", False)
            and "reveal_prompt" in self.state.completed_event_ids
            and "reveal_hint" not in self.state.completed_event_ids
            and not self.state.resolved
        ):
            return REVEAL_HINT_RESPONSE
        return None

    def synchronize_situation(self, situation: NarrativeSituation) -> NarrativeSituation:
        """Render naming into the public narrative state once, in case
        fixed_line_for's proposed_name branch settled it: that branch skips
        interpret() entirely (see forward()'s fixed_line_for contract), so
        nothing else applies the usual remove NAMING_TOPIC / add
        APPEARANCE_UNRESOLVED delta for it. Gated on NAMING_TOPIC still being
        present so an ordinary turn, whose own situation_update already
        applied this, never gets a second, differently-worded naming event.

        Also restores APPEARANCE_UNRESOLVED if interpret() dropped it without
        the appearance actually being settled (confirmed live: affirming the
        chosen name, in the same turn Pooh's own line happened to ask about
        appearance, was enough for interpret() to report remove_unresolved
        for it with no settled_details at all). Once the topic is missing
        from the model's own view, a later turn that does describe the
        appearance free-texts a topic label keyed to the creature's name
        instead of anchoring on the real unresolved text, which
        observe_settled_details then never matches either — so ask_appearance
        re-asked a third time. self.state.appearance_settled is the only
        ground truth for whether this is genuinely done."""
        if self.state.named and NAMING_TOPIC in situation.unresolved:
            situation = apply_situation_update(
                situation,
                SituationUpdate(
                    add_events=[f"足跡の主を「{self.state.name}」と名付けた"],
                    remove_unresolved=[NAMING_TOPIC],
                    add_unresolved=(
                        [] if self.state.appearance_settled else [APPEARANCE_UNRESOLVED]
                    ),
                ),
            )
        elif (
            self.state.named
            and not self.state.appearance_settled
            and APPEARANCE_UNRESOLVED not in situation.unresolved
        ):
            situation = apply_situation_update(
                situation, SituationUpdate(add_unresolved=[APPEARANCE_UNRESOLVED]),
            )
        return situation

    def _twist_still_hidden(self) -> bool:
        return not self.state.resolved and "reveal_direct" not in self.state.completed_event_ids

    def response_may_be_replaced(self, user_action: str, history: str) -> bool:
        """Hold the line back (no early streaming) from fourth_animal until
        the twist is out: both leaks seen live (today's "僕たちのだった" and
        the earlier "ぼくたちの影", see pooh_footprints_examples.py) came right
        after the count reached eight, when Pooh is pressed to explain it.
        Holding from start_following instead would cover most of the story
        and delay nearly every line. Before fourth_animal a leak is still
        replaced, but if it was already streamed the robot has said it
        (NarrativeSession.submit warns); without a robot nothing is streamed,
        so the replacement always applies."""
        return (
            "fourth_animal" in self.state.completed_event_ids
            and self._twist_still_hidden()
        )

    def enforce_response_invariants(
        self, user_action: str, history: str, bot_response: str,
        narrative_actions: list[str] | tuple[str, ...] = (),
    ) -> str:
        """Replace a generated line that gives away the twist before the
        participant raised it and before reveal_direct, as interpret() judged
        it (pooh_suggested_own_footprints). Once the participant suggested it
        themselves, Pooh may agree."""
        if (
            self._twist_still_hidden()
            and not self._participant_suggested_own_footprints
            and "pooh_suggested_own_footprints" in narrative_actions
        ):
            return TWIST_LEAK_REPLACEMENT
        return bot_response

    def extra_actions_for(self, bot_response: str) -> list[str]:
        """narrative_actions to record as if the model had reported them,
        when enforce_response_invariants replaced its line with one that
        implies an action the model's own (discarded) line might not have."""
        if bot_response == REVEAL_HINT_RESPONSE:
            return ["gave_hint_directly"]
        return []

    @staticmethod
    def _fire_idle_close(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("idle_close_after_reveal")
        return WorldEvent(
            event_id="idle_close_after_reveal",
            description="気づきのあと、参加者の沈黙が続いた。",
            scene_id="none",
            fallback_response="",
            situation_update=SituationUpdate(),
            fixed_response=True,
            ends_session=True,
        )
