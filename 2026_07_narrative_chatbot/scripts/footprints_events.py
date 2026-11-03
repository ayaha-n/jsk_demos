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
narrative_events.SettledDetail) only for naming, where the actual chosen
text (not just that naming happened) matters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from narrative_events import (
    NarrativeEventController,
    RequiredNarrativeEvent,
    SettledDetail,
    WorldEvent,
    fixed_utterance,
)
from narrative_state import SituationUpdate


FootprintsAction = Literal[
    "started_following",
    "noticed_second_animal",
    "noticed_fourth_animal",
    "realized_own_footprints",
]
KNOWN_FOOTPRINTS_ACTIONS = {
    "started_following",
    "noticed_second_animal",
    "noticed_fourth_animal",
    "realized_own_footprints",
}

# settled_details topic marking that Pooh has named the creature (see
# pooh_footprints_examples.py's 場面2); arms the start_following clock.
NAMING_TOPIC = "正体の呼び名"
# The scenario's main unresolved label; settling it (by the participant's own
# realization, or by _fire_reveal_direct) stops any further escalation.
IDENTITY_UNRESOLVED = "この足跡は何の生き物のものか"
# Raised once naming is decided (organically or by _fire_name_creature);
# resolved during 場面3's appearance discussion.
APPEARANCE_UNRESOLVED = "ウズウズの見た目はどんな様子か"

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
REVEAL_DIRECT_EVENT = "実はウズウズの足跡は自分たちの足跡だったとわかった"
REVEAL_DIRECT_RESPONSE = fixed_utterance("footprints.reveal_direct")


@dataclass
class FootprintsState:
    # The name actually used, whether Pooh invented it himself (organically
    # or via _fire_name_creature's "ウズウズ") or the participant proposed
    # it; settled_details' value is never interpreted, only kept as-is.
    name: str | None = None
    named: bool = False
    following_started: bool = False
    # Settled early (the participant's own realization) or by _fire_reveal_direct.
    resolved: bool = False
    completed_event_ids: set[str] = field(default_factory=set)


class FootprintsEventController(NarrativeEventController):
    """Own the clock for the footprint-count escalation and reveal cascade."""

    def __init__(
        self,
        naming_delay_seconds: float,
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
        if naming_delay_seconds < 0:
            raise ValueError("naming_delay_seconds must be non-negative")
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
        self.naming_delay_seconds = naming_delay_seconds
        self.start_following_delay_seconds = start_following_delay_seconds
        self.second_animal_delay_seconds = second_animal_delay_seconds
        self.fourth_animal_delay_seconds = fourth_animal_delay_seconds
        self.reveal_step_delay_seconds = reveal_step_delay_seconds
        super().__init__(
            state=FootprintsState(),
            required_events=required_events or self._default_required_events(
                naming_delay_seconds,
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
        naming_delay_seconds: float,
        start_following_delay_seconds: float,
        second_animal_delay_seconds: float,
        fourth_animal_delay_seconds: float,
        reveal_step_delay_seconds: float,
        idle_close_seconds: float | None = None,
    ) -> tuple[RequiredNarrativeEvent, ...]:
        events = (
            # Both beats below are free to happen organically (see
            # pooh_footprints_examples.py's 場面2/4); each is only the
            # fallback for when a small trainset's demos do not generalize to
            # whatever phrasing actually comes up, the same reason the beats
            # further down are Python-fired rather than learned.
            RequiredNarrativeEvent(
                event_id="name_creature",
                delay_seconds=naming_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: not state.named and not state.resolved,
                fire=FootprintsEventController._fire_name_creature,
            ),
            # Naming comes first: start_following only arms once it is done,
            # so this can never race ahead of it (see NarrativeEventController
            # ._arm_required_events — a deadline is set once, the first time
            # its prerequisite holds).
            RequiredNarrativeEvent(
                event_id="start_following",
                delay_seconds=start_following_delay_seconds,
                waits_for_quiet=True,
                prerequisite=lambda state: (
                    state.named and not state.following_started and not state.resolved
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
                    # The conversation moved on without ever naming it; the
                    # start_following fallback's own prerequisite requires
                    # state.named, so an organic report here must keep that
                    # invariant too instead of silently breaking it.
                    self.state.named = True
                    self.state.name = "ウズウズ"
                self.state.following_started = True
            elif action == "noticed_second_animal":
                self.state.completed_event_ids.add("second_animal")
            elif action == "noticed_fourth_animal":
                self.state.completed_event_ids.add("second_animal")
                self.state.completed_event_ids.add("fourth_animal")
            elif action == "realized_own_footprints":
                self.state.resolved = True
        self._arm_required_events()

    def observe_settled_details(self, details: list[SettledDetail | dict]) -> None:
        """Record the chosen name, whoever picked it; arms start_following."""
        for detail in details:
            detail = SettledDetail.model_validate(detail)
            if detail.topic == NAMING_TOPIC:
                self.state.named = True
                self.state.name = detail.value
        self._arm_required_events()

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
                add_unresolved=[APPEARANCE_UNRESOLVED],
            ),
            fixed_response=True,
        )

    @staticmethod
    def _fire_start_following(state: FootprintsState) -> WorldEvent:
        state.completed_event_ids.add("start_following")
        state.following_started = True
        return WorldEvent(
            event_id="start_following",
            description=(
                "プーが参加者を誘って、ウズウズの足跡を追いかけ始めた。"
                "これは既に起きた出来事。"
            ),
            scene_id="4",
            fallback_response=START_FOLLOWING_RESPONSE,
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
                add_props=["足あと（さらに増えている）"],
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
                remove_props=["足あと（さらに増えている）"],
                add_props=["足あと（４つ）"],
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
