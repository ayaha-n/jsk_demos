"""Deterministic state and scheduling for timed narrative events."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from narrative_state import NarrativeSituation, SituationUpdate, apply_situation_update


FIXED_UTTERANCES_PATH = Path(__file__).resolve().parents[1] / "config" / "fixed_utterances.json"


def _load_fixed_utterances() -> dict[str, dict[str, str]]:
    with FIXED_UTTERANCES_PATH.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("fixed_utterances.json must contain an object")
    result: dict[str, dict[str, str]] = {}
    texts: set[str] = set()
    for utterance_id, spec in data.items():
        if not isinstance(utterance_id, str) or not isinstance(spec, dict):
            raise ValueError("fixed utterance entries must map IDs to objects")
        text = spec.get("text")
        wav = spec.get("wav")
        if not isinstance(text, str) or not text:
            raise ValueError(f"fixed utterance {utterance_id!r} has no text")
        if wav != f"{utterance_id}.wav":
            raise ValueError(f"fixed utterance {utterance_id!r} has an unexpected wav name")
        if text in texts:
            raise ValueError(f"fixed utterance text is duplicated: {text!r}")
        texts.add(text)
        result[utterance_id] = {"text": text, "wav": wav}
    return result


FIXED_UTTERANCES = _load_fixed_utterances()


def fixed_utterance(utterance_id: str) -> str:
    return FIXED_UTTERANCES[utterance_id]["text"]


def fixed_utterance_id(text: str) -> str | None:
    return next(
        (utterance_id for utterance_id, spec in FIXED_UTTERANCES.items()
         if spec["text"] == text),
        None,
    )


NarrativeAction = Literal[
    "propose_honey_jar_gift",
    "commit_honey_jar_gift",
    "block_pooh_honey_access",
]

KNOWN_NARRATIVE_ACTIONS = {
    "propose_honey_jar_gift",
    "commit_honey_jar_gift",
    "block_pooh_honey_access",
}

class SettledDetail(BaseModel):
    """One detail that was settled in a turn, with what it settled on.

    Topics are open (a drink, the cake's decoration, ...); Python only keeps
    the latest value per topic and renders it, it never interprets the text.
    """

    model_config = ConfigDict(extra="forbid")

    topic: str
    value: str

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
# Once the honey jar has already been proposed, a sudden "そうだ！" would sound
# like a new idea, so the decision is voiced as settling on that proposal.
HONEY_GIFT_COMMITTED_AFTER_PROPOSAL_RESPONSE = fixed_utterance(
    "eeyore_birthday.honey_gift_committed.after_proposal"
)
HONEY_GIFT_COMMITTED_AFTER_PROPOSAL_FOLLOW_UP_RESPONSE = (
    fixed_utterance("eeyore_birthday.honey_gift_committed.after_proposal.follow_up")
)
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
HONEY_PREPARATION_UNRESOLVED = "ハチミツの準備をどう進めるか"
EMPTY_JAR_UNRESOLVED = "空になった壺をどうするか"
BALLOON_COLOR_UNRESOLVED = "贈り物にする風船の色"
RIBBON_COLOR_UNRESOLVED = "リボンの色"
BLOCKED_ACCESS_EVENT = "参加者が贈り物の蜂蜜を食べないよう明確に制止した"
# Settling either of these topics decides what Eeyore gets.  Once the honey
# is gone, that decision is what completes the story.
GIFT_TOPICS = (EMPTY_JAR_UNRESOLVED, GIFT_DECISION_UNRESOLVED[0])
STORY_WRAP_UP_EVENT = "イーヨーへの贈り物が決まった"
STORY_WRAP_UP_DESCRIPTION = (
    "空になった壺をどうするか（そのまま贈る、別の贈り物に替えるなど）が決まり、"
    "イーヨーへの贈り物が決まった。"
    "これは既に起きた出来事。"
)
# Pooh cannot move, so the wrap-up only reflects on the gift and leaves the
# choice to keep talking with the participant.
STORY_WRAP_UP_RESPONSE = fixed_utterance("eeyore_birthday.story_wrap_up")


# Measured from the pre-generated fixed-utterance WAVs (about 4.3 characters
# per second across all of them); used to wait for Pooh to finish speaking.
SPEECH_CHARS_PER_SECOND = 4.3


def estimate_speech_seconds(text: str) -> float:
    """Rough playback length of a Japanese line spoken by Pooh."""
    return len(text.strip()) / SPEECH_CHARS_PER_SECOND


@dataclass(frozen=True)
class WorldEvent:
    event_id: str
    description: str
    scene_id: str
    fallback_response: str
    situation_update: SituationUpdate
    fixed_response: bool = False
    # Fixed line used when the event follows Pooh's answer to the participant
    # instead of filling a silence; None reuses fallback_response.
    follow_up_response: str | None = None
    # The session closes with the scenario's ending line instead of speaking.
    ends_session: bool = False


@dataclass(frozen=True)
class RequiredNarrativeEvent:
    """A named event that fires once its delay has passed and its prerequisite holds.

    An event that ``waits_for_quiet`` opens a new story beat, so it does not
    interrupt an ongoing exchange: it fires alone after ``quiet_seconds`` of
    silence, or right after Pooh's next answer, whichever comes first.
    """

    event_id: str
    delay_seconds: float
    waits_for_quiet: bool
    prerequisite: Callable[["HoneyGiftState"], bool]
    fire: Callable[["HoneyGiftState"], WorldEvent]
    # Overrides the controller's quiet_seconds for this event.
    quiet_seconds: float | None = None
    # False for events that must only fill a silence, never follow an answer.
    follow_up_allowed: bool = True


@dataclass
class HoneyGiftState:
    gift_status: str = "undecided"
    honey_gift_proposed: bool = False
    honey_status: str = "full"
    access_restriction: str = "none"
    completed_event_ids: set[str] = field(default_factory=set)
    # Settled gift details by topic (the unresolved label they answer).
    settled_details: dict[str, str] = field(default_factory=dict)
    # What Eeyore gets instead, decided after the honey was eaten.
    replacement_gift: str | None = None


class HoneyGiftEventController:
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
    ) -> None:
        if gift_decision_delay_seconds < 0:
            raise ValueError("gift_decision_delay_seconds must be non-negative")
        if tasting_delay_seconds < 0:
            raise ValueError("tasting_delay_seconds must be non-negative")
        if eating_delay_seconds < 0:
            raise ValueError("eating_delay_seconds must be non-negative")
        if quiet_seconds < 0:
            raise ValueError("quiet_seconds must be non-negative")
        if idle_close_seconds is not None and idle_close_seconds < 0:
            raise ValueError("idle_close_seconds must be non-negative")
        self.gift_decision_delay_seconds = gift_decision_delay_seconds
        self.tasting_delay_seconds = tasting_delay_seconds
        self.eating_delay_seconds = eating_delay_seconds
        self.quiet_seconds = quiet_seconds
        self.clock = clock
        self.state = HoneyGiftState()
        self.required_events = required_events or self._default_required_events(
            gift_decision_delay_seconds,
            tasting_delay_seconds,
            eating_delay_seconds,
            idle_close_seconds,
        )
        self._deadlines: dict[str, float] = {}
        self.speech_seconds = speech_seconds
        self._last_activity = clock()
        # When Pooh's latest line is expected to finish playing.  Delays and
        # quiet time count from here, not from when the text was generated.
        self._speech_end = self._last_activity
        self._arm_required_events()

    @staticmethod
    def _default_required_events(
        gift_decision_delay_seconds: float,
        tasting_delay_seconds: float,
        eating_delay_seconds: float,
        idle_close_seconds: float | None = None,
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
                    and state.access_restriction != "blocked"
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
                    and state.access_restriction != "blocked"
                    and "pooh_tastes_honey" in state.completed_event_ids
                ),
                fire=HoneyGiftEventController._fire_honey_eating,
            ),
            RequiredNarrativeEvent(
                event_id="story_wrap_up",
                delay_seconds=0.0,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    state.replacement_gift is not None
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

    def _arm_required_events(self) -> None:
        start = max(self.clock(), self._speech_end)
        for event in self.required_events:
            if (
                event.event_id not in self.state.completed_event_ids
                and event.prerequisite(self.state)
                and event.event_id not in self._deadlines
            ):
                self._deadlines[event.event_id] = start + event.delay_seconds

    def observe_activity(self, speech_seconds: float = 0.0) -> None:
        """Record participant input, or Pooh's line with its playback length.

        Deadlines already set never move; events armed afterwards, and quiet
        time, count from the end of that speech.
        """
        now = self.clock()
        self._speech_end = max(self._speech_end, now + speech_seconds)
        self._last_activity = max(now, self._speech_end)

    def begin(self, opening_speech_seconds: float) -> None:
        """Start the story clock once the scripted opening has been spoken."""
        self._deadlines.clear()
        self.observe_activity(opening_speech_seconds)
        self._arm_required_events()

    def _ready_at(self, event: RequiredNarrativeEvent, follow_up: bool) -> float:
        deadline = self._deadlines[event.event_id]
        if follow_up and not event.follow_up_allowed:
            return float("inf")
        if follow_up:
            # A follow-up is queued right behind the answer still being spoken,
            # so the wait for that speech does not apply to it.
            return deadline - max(0.0, self._speech_end - self.clock())
        if event.waits_for_quiet and not follow_up:
            quiet = self.quiet_seconds if event.quiet_seconds is None else event.quiet_seconds
            return max(deadline, self._last_activity + quiet)
        return deadline

    def observe_actions(self, actions: list[str]) -> None:
        """Validate and apply LM-proposed actions without resetting equal decisions."""
        for action in actions:
            if action not in KNOWN_NARRATIVE_ACTIONS:
                continue
            if action == "propose_honey_jar_gift":
                # A proposal is not a decision; it only changes how Pooh later
                # voices his own decision.
                self.state.honey_gift_proposed = True
            elif action == "commit_honey_jar_gift":
                self._commit_gift()
            elif action == "block_pooh_honey_access":
                self.state.access_restriction = "blocked"
                self._cancel_schedule()
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
            # The empty jar question only exists once the honey is gone; an
            # early signal must not pre-settle it and skip the wrap-up.
            if topic == EMPTY_JAR_UNRESOLVED and self.state.honey_status != "empty":
                continue
            self.state.settled_details[topic] = value
            if topic in GIFT_TOPICS and self.state.honey_status == "empty":
                self.state.replacement_gift = value
        self._arm_required_events()

    def _commit_gift(self) -> None:
        state = self.state
        if state.gift_status == "committed":
            return
        if (
            state.honey_status != "full"
            or state.access_restriction == "blocked"
        ):
            return
        state.gift_status = "committed"
        state.completed_event_ids.add("honey_gift_committed")
        self._deadlines.pop("honey_gift_committed", None)
        self._arm_required_events()

    @staticmethod
    def _fire_honey_gift_commitment(state: HoneyGiftState) -> WorldEvent:
        state.gift_status = "committed"
        state.completed_event_ids.add("honey_gift_committed")
        if state.honey_gift_proposed:
            response = HONEY_GIFT_COMMITTED_AFTER_PROPOSAL_RESPONSE
            follow_up_response = HONEY_GIFT_COMMITTED_AFTER_PROPOSAL_FOLLOW_UP_RESPONSE
        else:
            response = HONEY_GIFT_COMMITTED_RESPONSE
            follow_up_response = HONEY_GIFT_COMMITTED_FOLLOW_UP_RESPONSE
        return WorldEvent(
            event_id="honey_gift_committed",
            description=HONEY_GIFT_COMMITTED_DESCRIPTION,
            scene_id="1c",
            fallback_response=response,
            situation_update=SituationUpdate(
                add_events=[HONEY_GIFT_COMMITTED_EVENT],
                remove_unresolved=["イーヨーに何をあげるか"],
                add_unresolved=[HONEY_PREPARATION_UNRESOLVED],
            ),
            fixed_response=True,
            follow_up_response=follow_up_response,
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
    def _fire_story_wrap_up(state: HoneyGiftState) -> WorldEvent:
        state.completed_event_ids.add("story_wrap_up")
        return WorldEvent(
            event_id="story_wrap_up",
            description=STORY_WRAP_UP_DESCRIPTION,
            scene_id="5",
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

    def _cancel_schedule(self) -> None:
        state = self.state
        self._deadlines.clear()

    def seconds_until_due(self) -> float | None:
        self._arm_required_events()
        ready_times = [
            self._ready_at(event, follow_up=False)
            for event in self.required_events
            if event.event_id in self._deadlines
        ]
        if not ready_times:
            return None
        return max(0.0, min(ready_times) - self.clock())

    def pop_due_event(self, follow_up: bool = False) -> WorldEvent | None:
        """Fire one ready event.

        ``follow_up`` is used right after Pooh answers the participant: an
        overdue event then fires without waiting for silence.
        """
        self._arm_required_events()
        now = self.clock()
        due = next(
            (event for event in self.required_events
             if event.event_id in self._deadlines
             and now >= self._ready_at(event, follow_up)
             and event.prerequisite(self.state)),
            None,
        )
        if due is None:
            return None
        self._deadlines.pop(due.event_id, None)
        result = due.fire(self.state)
        if result.fixed_response:
            line = result.fallback_response
            if follow_up and result.follow_up_response is not None:
                line = result.follow_up_response
            # The next beat (e.g. eating after tasting) waits for this line.
            self.observe_activity(self.speech_seconds(line))
        self._arm_required_events()
        return result

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
            if self.state.replacement_gift is None:
                # Only a settled detail closes this item, which in turn
                # triggers the wrap-up.  A reworded or dropped label in the
                # model's own delta must not end the thread silently.
                situation = apply_situation_update(
                    situation,
                    SituationUpdate(add_unresolved=[EMPTY_JAR_UNRESOLVED]),
                )
        if "honey_gift_committed" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(
                    add_events=[HONEY_GIFT_COMMITTED_EVENT],
                    remove_unresolved=list(GIFT_DECISION_UNRESOLVED),
                ),
            )
        if "pooh_tastes_honey" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(add_events=[HONEY_TASTED_EVENT]),
            )
        settled = self.state.settled_details
        closed = list(settled)
        if self.state.replacement_gift is not None:
            closed += list(GIFT_TOPICS)
        situation = apply_situation_update(
            situation, SituationUpdate(remove_unresolved=closed),
        ).model_copy(
            update={"decided": [f"{topic}：{value}" for topic, value in settled.items()]}
        )
        if "story_wrap_up" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(add_events=[STORY_WRAP_UP_EVENT]),
            )
        if self.state.access_restriction == "blocked":
            situation = apply_situation_update(
                situation,
                SituationUpdate(add_events=[BLOCKED_ACCESS_EVENT]),
            )
        return situation
