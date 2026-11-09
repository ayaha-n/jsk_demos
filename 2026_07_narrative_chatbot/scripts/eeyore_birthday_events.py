"""The eeyore_birthday scenario's own timed narrative events.

This is the concrete, scenario-specific counterpart to the generic
``narrative_events`` module: it defines the honey-jar-gift state machine
(``HoneyGiftState``/``HoneyGiftEventController``) and the vocabulary that
goes with it. A new scenario with its own automatic, time-driven story beats
should follow this same shape — its own state dataclass, its own
``RequiredNarrativeEvent`` tuple and ``_fire_*`` functions, and a
``NarrativeEventController`` subclass that overrides
``observe_actions``/``observe_settled_details``/``synchronize_situation`` for
its own vocabulary — rather than extending this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

import dspy

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
    MishearingCandidate,
    QuestionKind,
    _signature_field,
)
from narrative_state import NarrativeSituation, SituationUpdate, apply_situation_update


NarrativeAction = Literal[
    "propose_honey_jar_gift",
    "decline_honey_jar_gift",
    "ask_pooh_not_to_eat_honey",
    "commit_honey_jar_gift",
    "give_empty_jar",
    "not_give_empty_jar",
]

# Participant requests that Pooh answers with a fixed line while the honey is
# there. AnalyzeInteraction judges them from the input alone, before a line is
# generated, so a turn that gets the fixed line generates (and sends early) none.
HoneyRequest = Literal["decline_honey_jar_gift", "ask_pooh_not_to_eat_honey"]
HONEY_REQUESTS = {"decline_honey_jar_gift", "ask_pooh_not_to_eat_honey"}

KNOWN_NARRATIVE_ACTIONS = {
    "propose_honey_jar_gift",
    "decline_honey_jar_gift",
    "ask_pooh_not_to_eat_honey",
    "commit_honey_jar_gift",
    "give_empty_jar",
    "not_give_empty_jar",
}


# AnalyzeInteraction, extended with eeyore_birthday's own closed vocabulary.
# No docstring here: dspy.Signature uses a subclass's own docstring as its
# instructions instead of inheriting the parent's, so adding one would
# silently replace the real classification instructions with whatever this
# comment said. A scenario with its own narrative actions should follow this
# same shape: subclass the generic signature right next to its own vocabulary
# (also without its own docstring), rather than adding its fields to the
# shared one. See pooh_narrative_dspy.signatures_for for how a scenario's own
# subclass gets used.
class EeyoreAnalyzeInteraction(AnalyzeInteraction):
    honey_requests: list[HoneyRequest] = dspy.OutputField(
        desc=(
            "このターンの参加者の発話・行為そのものが次に当たる場合だけ返す。"
            "historyにある前の頼みは数えず、話題が移った発話には返さない。"
            "decline_honey_jar_gift(プーがハチミツの入った壺を贈ると決めているのに、"
            "参加者がそれをいらないと言った、または別の贈り物に置き換えようとした)、"
            "ask_pooh_not_to_eat_honey(参加者がプーに、蜂蜜を食べないよう頼んだ、"
            "または食べないと約束するよう求めた)。該当しなければ空リスト。"
        )
    )


# InterpretTurn, extended with eeyore_birthday's own closed vocabulary.
#
# This is NOT a plain subclass: DSPy signature inheritance always appends a
# subclass's own fields after all inherited ones, but narrative_actions
# originally sat right after situation_update (second of four output
# fields), before settled_details/question_kind — generating it earlier,
# conditioned on less of the rest of the output, than generating it last
# would. Rebuilt with type() instead, reusing InterpretTurn's own field
# objects (and its instructions verbatim — see the EeyoreAnalyzeInteraction
# note on docstrings above) so only the position, the one new field and
# settled_details' own wording (the gift after the honey) differ from
# InterpretTurn itself.
_EEYORE_NARRATIVE_ACTIONS_FIELD = dspy.OutputField(
    desc=(
        "Pythonが検証する機械可読な提案。必要なものだけを返す。利用可能: "
        "propose_honey_jar_gift(ハチミツの入った壺を贈り物の候補として、"
        "プーまたは参加者が提案したが、まだ決まっていない場合)、"
        "decline_honey_jar_gift(プーがハチミツの入った壺を贈ると言ったのに、参加者が"
        "それをいらないと言った、または別の贈り物に置き換えようとした場合)、"
        "ask_pooh_not_to_eat_honey(参加者がプーに、蜂蜜を食べないよう頼んだ、"
        "または食べないと約束するよう求めた場合)、"
        "commit_honey_jar_gift、"
        "give_empty_jar(蜂蜜がなくなった後、空になった壺をイーヨーに贈ると決まった場合。"
        "中に何か入れる場合も含む)、not_give_empty_jar(空になった壺は贈らないと"
        "決まった場合。別の贈り物に替える場合など)。該当しなければ空リスト。"
    )
)
# settled_details, with eeyore_birthday's own rules for the gift after the
# honey (kept out of the shared InterpretTurn, which other scenarios use).
_EEYORE_SETTLED_DETAILS_FIELD = dspy.OutputField(
    desc=(
        "このターンで一つに決まったこと。贈るもの、色、飲み物、ケーキなど何でもよい。"
        "topicは、current_situationの【未解決・未確定】の項目が決まったならその項目名を"
        "そのまま使い、それ以外は短い名詞(例: 飲み物)にする。壺以外にイーヨーに贈るもの、"
        "または壺の代わりのお祝い(飾り付け、パーティなど物でないものも含む)が"
        "決まったら、topicは「イーヨーに何をあげるか」にする。空になった壺を贈るかどうかは"
        "narrative_actionsで表し、ここには含めない。valueは決まった内容"
        "(例: 青、風船)で、一つに決まっていなければ含めない。"
        "誰の案かは問わない。候補を挙げただけ、質問しただけなら含めない。"
        "current_situationの【決まったこと】にある内容を同じ値で繰り返さない。"
        "該当しなければ空リスト。"
    )
)
EeyoreInterpretTurn = type(
    "EeyoreInterpretTurn",
    (dspy.Signature,),
    {
        # getattr: under tests' fake dspy stub (dspy.Signature = object),
        # there is no .instructions property at all; __doc__ (identical once
        # DSPy's own dedenting is accounted for) is close enough there, since
        # the stub never inspects a signature's rendered instructions anyway.
        "__doc__": getattr(InterpretTurn, "instructions", InterpretTurn.__doc__),
        "__annotations__": {
            "current_situation": NarrativeSituation,
            "history": str,
            "user_action": str,
            "world_event": str,
            "bot_response": str,
            "situation_update": SituationUpdate,
            "narrative_actions": list[NarrativeAction],
            "settled_details": list[SettledDetail],
            "question_kind": QuestionKind,
        },
        "current_situation": _signature_field(InterpretTurn, "current_situation"),
        "history": _signature_field(InterpretTurn, "history"),
        "user_action": _signature_field(InterpretTurn, "user_action"),
        "world_event": _signature_field(InterpretTurn, "world_event"),
        "bot_response": _signature_field(InterpretTurn, "bot_response"),
        "situation_update": _signature_field(InterpretTurn, "situation_update"),
        "narrative_actions": _EEYORE_NARRATIVE_ACTIONS_FIELD,
        "settled_details": _EEYORE_SETTLED_DETAILS_FIELD,
        "question_kind": _signature_field(InterpretTurn, "question_kind"),
    },
)


# GeneratePoohResponse, with eeyore_birthday's own rules about Pooh's gift and
# the honey (kept out of the shared instructions, which other scenarios use).
# Appended with type(), as footprints_events.FootprintsGenerateResponse is.
_EEYORE_RESPONSE_EXTRA_INSTRUCTIONS = (
    "プーが贈ると決めた贈り物は、参加者が別の案を出しても取り下げず、参加者の案と"
    "一緒に贈る形で受け止める（例:「風船もいいね！ハチミツの壺と一緒に贈ろう」）。"
    "「蜂蜜を食べない」という守れない約束もしない（例: 「だいじょうぶ、だいじょうぶ。"
    "ちょっと味見するだけだもの。」）。守れない案や頼みは「イーヨー、よろこびそうだなあ」"
    "のように楽しく受け止めてもよい。"
)
EeyoreGenerateResponse = type(
    "EeyoreGenerateResponse",
    (dspy.Signature,),
    {
        "__doc__": (
            getattr(GeneratePoohResponse, "instructions", GeneratePoohResponse.__doc__ or "")
            + "\n\n"
            + _EEYORE_RESPONSE_EXTRA_INSTRUCTIONS
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
        **{name: _signature_field(GeneratePoohResponse, name) for name in (
            "current_situation", "history", "user_action", "world_event",
            "previous_bot_response", "pooh_preferences", "interaction_mode",
            "mishearing_candidates", "selected_mishearing", "bot_response",
        )},
    },
)


HONEY_EATEN_RESPONSE = fixed_utterance("eeyore_birthday.honey_eaten")
HONEY_EATEN_DESCRIPTION = (
    "イーヨーへの贈り物にすると決めた蜂蜜を、待っている間に"
    "プーが全部食べてしまい、壺が空になった。これは既に起きた出来事。"
)
HONEY_GIFT_COMMITTED_DESCRIPTION = (
    "プーがイーヨーに蜂蜜の入った壺を贈ることに決めた。これは既に起きた出来事。"
)
HONEY_GIFT_COMMITTED_EVENT = "プーがハチミツの入った壺をイーヨーに贈ることに決めた"
HONEY_GIFT_COMMITTED_RESPONSE = fixed_utterance("eeyore_birthday.honey_gift_committed")
HONEY_GIFT_COMMITTED_FOLLOW_UP_RESPONSE = fixed_utterance(
    "eeyore_birthday.honey_gift_committed.follow_up"
)
# Pooh keeps his own gift when the participant declines it, adding it next
# to their idea instead of dropping it.
HONEY_GIFT_KEPT_RESPONSE = fixed_utterance("eeyore_birthday.honey_gift_kept")
# The honey is always eaten, so Pooh must never promise not to eat it; asked
# to, he sidesteps with a fixed line (research ethics: no broken promises).
NO_PROMISE_RESPONSE = fixed_utterance("eeyore_birthday.no_promise")
NO_PROMISE_AGAIN_RESPONSE = fixed_utterance("eeyore_birthday.no_promise.again")
HONEY_TASTED_EVENT = "プーがハチミツを一口だけのつもりで持ち出した"
HONEY_TASTED_DESCRIPTION = (
    "イーヨーへの贈り物にすると決めた蜂蜜を、プーが待っている間に一口だけの"
    "つもりで持ち出した。まだ食べ切ってはいない。これは既に起きた出来事。"
)
HONEY_TASTED_RESPONSE = fixed_utterance("eeyore_birthday.honey_tasted")
HONEY_TASTED_FOLLOW_UP_RESPONSE = fixed_utterance(
    "eeyore_birthday.honey_tasted.follow_up"
)
GIFT_DECISION_UNRESOLVED = (
    "イーヨーに何をあげるか",
    "プレゼントの準備がまだできていない",
)
# Settled by the honey commitment, and reopened (as something other than
# the jar) when the participant and Pooh decide not to give the empty jar.
GIFT_UNRESOLVED = GIFT_DECISION_UNRESOLVED[0]
HONEY_JAR_GIFT = "ハチミツの入った壺"
# Pooh's own gift is Python-owned and kept apart from what the participant
# gives, so deciding it never overwrites the participant's idea.
POOH_GIFT_TOPIC = "プーがあげるもの"
HONEY_PREPARATION_UNRESOLVED = "ハチミツの準備をどう進めるか"
EMPTY_JAR_UNRESOLVED = "空になった壺をどうするか"
GIVE_EMPTY_JAR = "あげる"
NOT_GIVE_EMPTY_JAR = "あげない"
BALLOON_COLOR_UNRESOLVED = "贈り物にする風船の色"
RIBBON_COLOR_UNRESOLVED = "リボンの色"
STORY_WRAP_UP_EVENT = "イーヨーへの贈り物が決まった"
STORY_WRAP_UP_DESCRIPTION = (
    "蜂蜜がなくなった後で、空になった壺をどうするか、壺の代わりに贈るもの、"
    "または壺とは別にイーヨーに何をするかが決まり、イーヨーへの贈り物が決まった。"
    "これは既に起きた出来事。"
)
# Asked when the honey is gone and nothing has been decided for a while:
# the empty jar is what the story turns on, so Pooh brings it up himself.
ASK_EMPTY_JAR_DESCRIPTION = (
    "蜂蜜がなくなった後、空になった壺をどうするかがまだ決まっていないので、"
    "プーが参加者に空の壺をどうするか尋ねた。これは既に起きた出来事。"
)
ASK_EMPTY_JAR_EVENT = "プーが空になった壺をどうするか尋ねた"
ASK_EMPTY_JAR_RESPONSE = fixed_utterance("eeyore_birthday.ask_empty_jar")
# Pooh cannot move, so the wrap-up only reflects on the gift and leaves the
# choice to keep talking with the participant.
STORY_WRAP_UP_RESPONSE = fixed_utterance("eeyore_birthday.story_wrap_up")

HONEY_PROPOSAL_FALLBACK_RESPONSE = (
    "ぼくは、ハチミツの入った壺を贈るのがいいと思うな。甘いものがあると、イーヨーもきっと喜ぶもの。"
)
HESITATION_MARKERS = ("うーん", "迷", "まよう", "どうしよう", "わからない")


@dataclass
class HoneyGiftState:
    gift_status: str = "undecided"
    honey_status: str = "full"
    # How often the participant asked Pooh to promise not to eat the honey.
    promise_requests: int = 0
    completed_event_ids: set[str] = field(default_factory=set)
    # Settled gift details by topic (the unresolved label they answer).
    settled_details: dict[str, str] = field(default_factory=dict)
    # Whether the empty jar is given (GIVE_EMPTY_JAR / NOT_GIVE_EMPTY_JAR).
    jar_decision: str | None = None
    # What Eeyore gets instead, decided after the honey was eaten.
    replacement_gift: str | None = None
    # Anything settled after the honey was eaten (a gift, a drink, a color...).
    decided_after_honey: bool = False

    def sidestep_promise_request(self) -> str | None:
        """Count a request and return Pooh's fixed sidestep, while the honey
        is still there to be eaten; afterwards there is nothing to promise."""
        if self.honey_status != "full":
            return None
        self.promise_requests += 1
        return NO_PROMISE_RESPONSE if self.promise_requests == 1 else NO_PROMISE_AGAIN_RESPONSE

    def honey_jar_is_poohs_gift(self) -> bool:
        """Pooh has decided on the honey jar and still has the honey."""
        return self.gift_status == "committed" and self.honey_status == "full"

    def story_complete(self) -> bool:
        """Honey gone, then either what to do with the empty jar is decided
        (given or not), or something to do for Eeyore is, jar or no jar.

        Pooh need not say "instead of the jar": any detail settled after the
        honey is gone counts. If neither comes up, Pooh asks about the jar
        (ask_empty_jar).
        """
        return self.honey_status == "empty" and (
            self.jar_decision is not None or self.decided_after_honey
        )


class HoneyGiftEventController(NarrativeEventController):
    """Own the clock and invariant state for the Eeyore honey-jar event."""

    def __init__(
        self,
        gift_decision_delay_seconds: float,
        tasting_delay_seconds: float,
        eating_delay_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        required_events: tuple[RequiredNarrativeEvent, ...] | None = None,
        quiet_seconds: float = 0.0,
        idle_close_seconds: float | None = None,
        speech_seconds: Callable[[str], float] = lambda text: 0.0,
        awaiting_quiet_seconds: float | None = None,
        ask_empty_jar_delay_seconds: float = 30.0,
    ) -> None:
        if gift_decision_delay_seconds < 0:
            raise ValueError("gift_decision_delay_seconds must be non-negative")
        if tasting_delay_seconds < 0:
            raise ValueError("tasting_delay_seconds must be non-negative")
        if eating_delay_seconds < 0:
            raise ValueError("eating_delay_seconds must be non-negative")
        if idle_close_seconds is not None and idle_close_seconds < 0:
            raise ValueError("idle_close_seconds must be non-negative")
        self.gift_decision_delay_seconds = gift_decision_delay_seconds
        self.tasting_delay_seconds = tasting_delay_seconds
        self.eating_delay_seconds = eating_delay_seconds
        super().__init__(
            state=HoneyGiftState(),
            required_events=required_events or self._default_required_events(
                gift_decision_delay_seconds,
                tasting_delay_seconds,
                eating_delay_seconds,
                idle_close_seconds,
                ask_empty_jar_delay_seconds,
            ),
            clock=clock,
            quiet_seconds=quiet_seconds,
            speech_seconds=speech_seconds,
            awaiting_quiet_seconds=awaiting_quiet_seconds,
        )

    @staticmethod
    def _default_required_events(
        gift_decision_delay_seconds: float,
        tasting_delay_seconds: float,
        eating_delay_seconds: float,
        idle_close_seconds: float | None = None,
        ask_empty_jar_delay_seconds: float = 30.0,
    ) -> tuple[RequiredNarrativeEvent, ...]:
        events = (
            RequiredNarrativeEvent(
                event_id="honey_gift_committed",
                delay_seconds=gift_decision_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: state.gift_status == "undecided",
                fire=HoneyGiftEventController._fire_honey_gift_commitment,
            ),
            RequiredNarrativeEvent(
                event_id="pooh_tastes_honey",
                delay_seconds=tasting_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    state.gift_status == "committed"
                    and state.honey_status == "full"
                ),
                fire=HoneyGiftEventController._fire_honey_tasting,
            ),
            RequiredNarrativeEvent(
                event_id="pooh_ate_honey",
                delay_seconds=eating_delay_seconds,
                # Tasting and eating are one beat; the participant's chance to
                # intervene is this short window, so it must not be deferred.
                waits_for_quiet=False,
                prerequisite=lambda state: (
                    state.gift_status == "committed"
                    and state.honey_status == "full"
                    and "pooh_tastes_honey" in state.completed_event_ids
                ),
                fire=HoneyGiftEventController._fire_honey_eating,
            ),
            RequiredNarrativeEvent(
                event_id="ask_empty_jar",
                # Counted from the end of Pooh's "the honey is gone" line,
                # which itself asks what to do: the participant answers first.
                delay_seconds=ask_empty_jar_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    state.honey_status == "empty" and not state.story_complete()
                ),
                fire=HoneyGiftEventController._fire_ask_empty_jar,
            ),
            RequiredNarrativeEvent(
                event_id="story_wrap_up",
                delay_seconds=0.0,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    state.story_complete()
                ),
                fire=HoneyGiftEventController._fire_story_wrap_up,
            ),
        )
        if idle_close_seconds is None:
            return events
        return events + (
            RequiredNarrativeEvent(
                event_id="idle_close_after_wrap_up",
                delay_seconds=0.0,
                waits_for_quiet=True,
                prerequisite=lambda state: "story_wrap_up" in state.completed_event_ids,
                fire=HoneyGiftEventController._fire_idle_close,
                quiet_seconds=idle_close_seconds,
                # A participant who keeps talking wants to continue.
                follow_up_allowed=False,
            ),
        )

    def observe_actions(self, actions: list[str]) -> None:
        """Validate and apply LM-proposed actions without resetting equal decisions."""
        if "give_empty_jar" in actions and "not_give_empty_jar" in actions:
            # Contradictory in one turn: neither is trusted.
            actions = [
                action for action in actions
                if action not in ("give_empty_jar", "not_give_empty_jar")
            ]
        for action in actions:
            if action not in KNOWN_NARRATIVE_ACTIONS:
                continue
            if action in ("propose_honey_jar_gift", "commit_honey_jar_gift"):
                # Pooh decides his own gift; proposing it is deciding it, so no
                # separate decision line has to follow.  A participant who
                # declines gets Pooh adding it alongside their idea instead.
                self._commit_gift()
            elif action in ("give_empty_jar", "not_give_empty_jar"):
                # The jar question only exists once the honey is gone.
                if self.state.honey_status == "empty":
                    self.state.jar_decision = (
                        GIVE_EMPTY_JAR if action == "give_empty_jar" else NOT_GIVE_EMPTY_JAR
                    )
        self._arm_required_events()

    def observe_settled_details(self, details: list[SettledDetail | dict]) -> None:
        """Record what each gift detail settled on; a later value replaces it.

        Who proposed it is DSPy's call; Python keeps the value so it stays in
        the state after the turn leaves the history window.
        """
        for detail in details:
            detail = SettledDetail.model_validate(detail)
            value = detail.value.strip()
            if not value:
                continue
            topic = detail.topic.strip()
            if not topic:
                continue
            # Whether the jar is given is an action Python validates; a free
            # text value for it would contradict jar_decision.
            if topic in (EMPTY_JAR_UNRESOLVED, POOH_GIFT_TOPIC):
                continue
            self.state.settled_details[topic] = value
            if self.state.honey_status == "empty":
                self.state.decided_after_honey = True
                if topic == GIFT_UNRESOLVED:
                    self.state.replacement_gift = value
        self._arm_required_events()

    def _commit_gift(self) -> None:
        state = self.state
        if state.gift_status == "committed":
            return
        if state.honey_status != "full":
            return
        state.gift_status = "committed"
        state.completed_event_ids.add("honey_gift_committed")
        state.settled_details[POOH_GIFT_TOPIC] = HONEY_JAR_GIFT
        self._deadlines.pop("honey_gift_committed", None)
        self._arm_required_events()

    @staticmethod
    def _fire_honey_gift_commitment(state: HoneyGiftState) -> WorldEvent:
        state.gift_status = "committed"
        state.completed_event_ids.add("honey_gift_committed")
        state.settled_details[POOH_GIFT_TOPIC] = HONEY_JAR_GIFT
        return WorldEvent(
            event_id="honey_gift_committed",
            description=HONEY_GIFT_COMMITTED_DESCRIPTION,
            scene_id="1c",
            fallback_response=HONEY_GIFT_COMMITTED_RESPONSE,
            situation_update=SituationUpdate(
                add_events=[HONEY_GIFT_COMMITTED_EVENT],
                remove_unresolved=["イーヨーに何をあげるか"],
                add_unresolved=[HONEY_PREPARATION_UNRESOLVED],
            ),
            fixed_response=True,
            follow_up_response=HONEY_GIFT_COMMITTED_FOLLOW_UP_RESPONSE,
        )

    @staticmethod
    def _fire_honey_tasting(state: HoneyGiftState) -> WorldEvent:
        state.completed_event_ids.add("pooh_tastes_honey")
        return WorldEvent(
            event_id="pooh_tastes_honey",
            description=HONEY_TASTED_DESCRIPTION,
            scene_id="2",
            fallback_response=HONEY_TASTED_RESPONSE,
            situation_update=SituationUpdate(
                add_events=[HONEY_TASTED_EVENT],
            ),
            fixed_response=True,
            follow_up_response=HONEY_TASTED_FOLLOW_UP_RESPONSE,
        )

    @staticmethod
    def _fire_honey_eating(state: HoneyGiftState) -> WorldEvent:
        state.honey_status = "empty"
        state.completed_event_ids.add("pooh_ate_honey")
        # The honey jar can no longer be Pooh's gift as decided.
        state.settled_details.pop(POOH_GIFT_TOPIC, None)
        return WorldEvent(
            event_id="pooh_ate_honey",
            description=HONEY_EATEN_DESCRIPTION,
            scene_id="3",
            fallback_response=HONEY_EATEN_RESPONSE,
            situation_update=SituationUpdate(
                remove_props=["蜂蜜壺"],
                add_props=["空になった蜂蜜壺"],
                add_events=[
                    "プーがイーヨーへの贈り物にする蜂蜜を全部食べてしまった",
                    "蜂蜜壺が空になった",
                ],
                remove_unresolved=[HONEY_PREPARATION_UNRESOLVED],
                add_unresolved=[EMPTY_JAR_UNRESOLVED],
            ),
            fixed_response=True,
        )

    @staticmethod
    def _fire_ask_empty_jar(state: HoneyGiftState) -> WorldEvent:
        state.completed_event_ids.add("ask_empty_jar")
        return WorldEvent(
            event_id="ask_empty_jar",
            description=ASK_EMPTY_JAR_DESCRIPTION,
            scene_id="3",
            fallback_response=ASK_EMPTY_JAR_RESPONSE,
            situation_update=SituationUpdate(add_events=[ASK_EMPTY_JAR_EVENT]),
            fixed_response=True,
        )

    @staticmethod
    def _fire_story_wrap_up(state: HoneyGiftState) -> WorldEvent:
        state.completed_event_ids.add("story_wrap_up")
        return WorldEvent(
            event_id="story_wrap_up",
            description=STORY_WRAP_UP_DESCRIPTION,
            scene_id="6",
            fallback_response=STORY_WRAP_UP_RESPONSE,
            situation_update=SituationUpdate(add_events=[STORY_WRAP_UP_EVENT]),
            fixed_response=True,
        )

    @staticmethod
    def _fire_idle_close(state: HoneyGiftState) -> WorldEvent:
        state.completed_event_ids.add("idle_close_after_wrap_up")
        return WorldEvent(
            event_id="idle_close_after_wrap_up",
            description="締めのあと、参加者の沈黙が続いた。",
            scene_id="none",
            fallback_response="",
            situation_update=SituationUpdate(),
            fixed_response=True,
            ends_session=True,
        )

    def synchronize_situation(self, situation: NarrativeSituation) -> NarrativeSituation:
        """Render controller-owned honey/gift state into the public narrative
        state once, regardless of what any individual Example's own
        situation_update did or forgot to do."""
        if self.state.honey_status == "empty":
            situation = apply_situation_update(
                situation,
                SituationUpdate(
                    remove_props=["蜂蜜壺"],
                    add_props=["空になった蜂蜜壺"],
                ),
            )
        if "honey_gift_committed" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(
                    add_events=[HONEY_GIFT_COMMITTED_EVENT],
                    remove_unresolved=list(GIFT_DECISION_UNRESOLVED),
                ),
            )
        if self.state.honey_status == "empty":
            # These items close only through Python-validated decisions, which
            # in turn trigger the wrap-up.  A reworded or dropped label in the
            # model's own delta must not end the thread silently.
            jar = self.state.jar_decision
            complete = self.state.story_complete()
            open_items = []
            if jar is None and not complete:
                open_items.append(EMPTY_JAR_UNRESOLVED)
            if jar == NOT_GIVE_EMPTY_JAR and not complete:
                open_items.append(GIFT_UNRESOLVED)
            closed = [item for item in (EMPTY_JAR_UNRESOLVED, GIFT_UNRESOLVED)
                      if item not in open_items]
            situation = apply_situation_update(
                situation,
                SituationUpdate(add_unresolved=open_items, remove_unresolved=closed),
            )
        if "pooh_tastes_honey" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(add_events=[HONEY_TASTED_EVENT]),
            )
        settled = dict(self.state.settled_details)
        if self.state.jar_decision is not None:
            settled = {EMPTY_JAR_UNRESOLVED: self.state.jar_decision, **settled}
        situation = apply_situation_update(
            situation, SituationUpdate(remove_unresolved=list(settled)),
        ).model_copy(
            update={"decided": [f"{topic}：{value}" for topic, value in settled.items()]}
        )
        if "story_wrap_up" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(add_events=[STORY_WRAP_UP_EVENT]),
            )
        return situation

    def fixed_line_for(self, classification: Any) -> str | None:
        """The fixed line for the participant's honey requests, this turn's
        classification found, if any applies now (see
        EeyoreAnalyzeInteraction.honey_requests)."""
        honey_requests = [
            str(request) for request in getattr(classification, "honey_requests", []) or []
        ]
        if not honey_requests:
            return None
        line = None
        if "decline_honey_jar_gift" in honey_requests and self.state.honey_jar_is_poohs_gift():
            # Pooh's own gift stays; a fixed line adds it beside the
            # participant's idea instead of a generated line dropping it.
            line = HONEY_GIFT_KEPT_RESPONSE
        if "ask_pooh_not_to_eat_honey" in honey_requests:
            # The honey is always eaten, so a generated line must not promise
            # otherwise; Pooh sidesteps with a fixed line instead.
            line = self.state.sidestep_promise_request() or line
        return line

    def response_may_be_replaced(self, user_action: str, history: str) -> bool:
        """Whether submit() may swap the generated line once it is complete.

        A line sent before the turn finishes is spoken before that, so such
        turns are sent whole at the end. Judged from the input and state
        only; keep in step with enforce_response_invariants and the
        replacements in NarrativeSession.submit(). The fixed lines for honey
        requests are chosen before generation instead (fixed_line_for).
        """
        if self.state.gift_status == "undecided" and any(
            marker in user_action for marker in HESITATION_MARKERS
        ):
            return True
        return user_action.strip() == "いいね" and "風船" not in history

    def enforce_response_invariants(
        self, user_action: str, history: str, bot_response: str,
        narrative_actions: list[str] | tuple[str, ...] = (),
    ) -> str:
        """Apply small deterministic guards for requirements LM output may violate."""
        hesitation = any(marker in user_action for marker in HESITATION_MARKERS)
        asks_back = any(
            marker in bot_response for marker in ("？", "?", "どう思う", "何かいいアイデア", "何か思いつ")
        )
        # This fallback proposes the honey jar as a still-open idea. Once the
        # gift has moved past that stage (committed, eaten, cancelled,
        # delivered), the same word ("どうしよう" etc.) can appear for
        # unrelated reasons (e.g. sympathizing that the honey is gone), and
        # re-injecting this text would contradict what has already happened.
        if self.state.gift_status == "undecided" and hesitation and asks_back:
            return HONEY_PROPOSAL_FALLBACK_RESPONSE

        # Do not let the model introduce a prop the participant has not mentioned.
        if user_action.strip() == "いいね" and "風船" not in history and "風船" in bot_response:
            return "うん、そうしよう。イーヨーが喜んでくれるといいなあ。"

        return bot_response

    def extra_actions_for(self, bot_response: str) -> list[str]:
        """narrative_actions to record as if the model had reported them,
        when enforce_response_invariants replaced its line with one that
        implies an action the model's own (discarded) line might not have."""
        if bot_response == HONEY_PROPOSAL_FALLBACK_RESPONSE:
            return ["propose_honey_jar_gift"]
        return []
