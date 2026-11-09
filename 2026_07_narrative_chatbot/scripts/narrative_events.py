"""Deterministic state and scheduling for timed narrative events.

This module is scenario-agnostic: it only knows about fixed utterances,
``WorldEvent``/``RequiredNarrativeEvent`` and the generic clock/deadline
mechanics in ``NarrativeEventController``. A scenario that wants its own
automatic, time-driven story beats (Pooh deciding something while the
participant is silent, and so on) defines its own state class and its own
``RequiredNarrativeEvent`` tuple, then either uses ``NarrativeEventController``
directly or subclasses it to interpret LM-proposed actions/details against
that state (see ``eeyore_birthday_events.HoneyGiftEventController`` for the
worked example to copy).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from narrative_state import NarrativeSituation, SituationUpdate


FIXED_UTTERANCES_PATH = Path(__file__).resolve().parents[1] / "config" / "fixed_utterances.json"


def _load_fixed_utterances() -> dict[str, dict[str, Any]]:
    with FIXED_UTTERANCES_PATH.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("fixed_utterances.json must contain an object")
    result: dict[str, dict[str, Any]] = {}
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
        # Optional body motion played with the line, as {part: motion name}.
        motion = spec.get("motion", {})
        if not isinstance(motion, dict) or not all(
            isinstance(part, str) and isinstance(name, str) for part, name in motion.items()
        ):
            raise ValueError(f"fixed utterance {utterance_id!r} has an invalid motion")
        # Optional arm gestures for the rest of a long line, after the one in
        # motion: the robot holds each a few seconds while it speaks.
        arm_next = spec.get("arm_next", [])
        if not isinstance(arm_next, list) or not all(isinstance(arm, str) for arm in arm_next):
            raise ValueError(f"fixed utterance {utterance_id!r} has an invalid arm_next")
        result[utterance_id] = {"text": text, "wav": wav, "motion": motion,
                                "arm_next": arm_next}
    return result


FIXED_UTTERANCES = _load_fixed_utterances()


def fixed_utterance(utterance_id: str) -> str:
    return FIXED_UTTERANCES[utterance_id]["text"]


def fixed_utterance_motion(utterance_id: str) -> dict[str, str]:
    """The body motion registered for a fixed line; empty when it has none."""
    return dict(FIXED_UTTERANCES.get(utterance_id, {}).get("motion", {}))


def fixed_utterance_arm_next(utterance_id: str) -> list[str]:
    """The arm gestures after the first for a long fixed line; often none."""
    return list(FIXED_UTTERANCES.get(utterance_id, {}).get("arm_next", []))


def fixed_utterance_id(text: str) -> str | None:
    return next(
        (utterance_id for utterance_id, spec in FIXED_UTTERANCES.items()
         if spec["text"] == text),
        None,
    )


class SettledDetail(BaseModel):
    """One detail that was settled in a turn, with what it settled on.

    Topics are open (a drink, the cake's decoration, ...); Python only keeps
    the latest value per topic and renders it, it never interprets the text.
    """

    model_config = ConfigDict(extra="forbid")

    topic: str
    value: str


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

    ``prerequisite``/``fire`` take the controller's own scenario-specific
    state object (whatever was passed to ``NarrativeEventController``);
    this module never looks inside it.
    """

    event_id: str
    delay_seconds: float
    waits_for_quiet: bool
    prerequisite: Callable[[Any], bool]
    fire: Callable[[Any], WorldEvent]
    # Overrides the controller's quiet_seconds for this event.
    quiet_seconds: float | None = None
    # False for events that must only fill a silence, never follow an answer.
    follow_up_allowed: bool = True


class NarrativeEventController:
    """Own the clock and fire a scenario's ``RequiredNarrativeEvent``s.

    This base class is scenario-agnostic: it only knows about deadlines,
    quiet time and firing events against whatever ``state`` object it was
    given. A scenario with its own narrative actions / settled-detail
    handling, or that needs to render its state back into
    ``NarrativeSituation`` outside of a fired event's own
    ``situation_update``, subclasses this and overrides
    ``observe_actions``/``observe_settled_details``/``synchronize_situation``;
    the base implementations of those three are no-ops (the last one is the
    identity). A scenario whose classify/generate stages need a runtime
    guard (a fixed line for certain classify-stage results, or a
    deterministic rewrite of a generated line) overrides
    ``fixed_line_for``/``response_may_be_replaced``/
    ``enforce_response_invariants``/``extra_actions_for`` instead; those
    four default to never firing. See
    ``eeyore_birthday_events.HoneyGiftEventController`` for the worked
    example of both sets of hooks.

    ``state`` must carry a ``completed_event_ids: set[str]`` attribute: a
    fired event's prerequisite can easily still hold afterwards (nothing
    else about the state had to change for it to fire), so this is the only
    thing that stops it firing again every time events are armed.
    """

    def __init__(
        self,
        state: Any,
        required_events: tuple[RequiredNarrativeEvent, ...],
        clock: Callable[[], float] = time.monotonic,
        quiet_seconds: float = 0.0,
        speech_seconds: Callable[[str], float] = lambda text: 0.0,
        awaiting_quiet_seconds: float | None = None,
    ) -> None:
        if quiet_seconds < 0:
            raise ValueError("quiet_seconds must be non-negative")
        self.state = state
        self.required_events = required_events
        self.quiet_seconds = quiet_seconds
        # Longer silence to wait for while Pooh's last line asked the
        # participant something, so a new beat does not cut off their reply.
        self.awaiting_quiet_seconds = (
            quiet_seconds if awaiting_quiet_seconds is None else awaiting_quiet_seconds
        )
        self._awaiting_reply = False
        self.clock = clock
        self._deadlines: dict[str, float] = {}
        self._playback_deadlines: dict[str, tuple[float, float]] = {}
        self.speech_seconds = speech_seconds
        self._last_activity = clock()
        # When Pooh's latest line is expected to finish playing.  Delays and
        # quiet time count from here, not from when the text was generated.
        self._speech_end = self._last_activity
        self._arm_required_events()

    def _arm_required_events(self) -> None:
        start = max(self.clock(), self._speech_end)
        for event in self.required_events:
            active = (
                event.event_id not in self.state.completed_event_ids
                and event.prerequisite(self.state)
            )
            if active and event.event_id not in self._deadlines:
                self._deadlines[event.event_id] = start + event.delay_seconds
                self._playback_deadlines[event.event_id] = (self._deadlines[event.event_id], start)
            elif not active:
                # Another event's firing (or settled state) can invalidate this
                # one's prerequisite before it ever fires itself (e.g. the
                # mystery resolving early stops the whole reveal cascade); an
                # armed deadline left behind would make seconds_until_due()
                # report "ready" forever while pop_due_event() keeps refusing
                # it (its own prerequisite check), busy-looping the caller.
                self._deadlines.pop(event.event_id, None)
                self._playback_deadlines.pop(event.event_id, None)

    def confirm_speech_finished(self) -> None:
        """Web audio ended: replace the estimate with the actual finish time.

        Newly armed beats get their full configured delay after playback.
        Older deadlines are unchanged, preserving overdue follow-ups.
        This is opt-in from the Web server; ROS keeps its estimated clock.
        """
        now = self.clock()
        for event_id, (deadline, speech_end) in self._playback_deadlines.items():
            if self._deadlines.get(event_id) == deadline:
                self._deadlines[event_id] = now + max(0.0, deadline - speech_end)
        self._playback_deadlines.clear()
        self._speech_end = self._last_activity = now

    def observe_activity(self, speech_seconds: float = 0.0, awaiting_reply: bool = False) -> None:
        """Record participant input, or Pooh's line with its playback length.

        Deadlines already set never move; events armed afterwards, and quiet
        time, count from the end of that speech.  ``awaiting_reply`` marks a
        line that asked the participant something; any later activity clears it.

        A line starts at ``_speech_end`` (when the previous one, if any, is
        due to finish), not at ``now``: a generated reply immediately
        followed by a fixed follow-up (pop_due_event's own call below) calls
        this twice back to back, and the two speech lengths must add up, not
        collapse to whichever happens to be longer, or quiet time and newly
        armed events would count as if only one of the two lines was ever
        spoken.
        """
        self._awaiting_reply = awaiting_reply
        now = self.clock()
        self._speech_end = max(now, self._speech_end) + speech_seconds
        self._last_activity = max(now, self._speech_end)

    def begin(self, opening_speech_seconds: float) -> None:
        """Start the story clock once the scripted opening has been spoken."""
        self._deadlines.clear()
        self._playback_deadlines.clear()
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
            if self._awaiting_reply:
                quiet = max(quiet, self.awaiting_quiet_seconds)
            return max(deadline, self._last_activity + quiet)
        return deadline

    def observe_actions(self, actions: list[str]) -> list[str]:
        """Hook: interpret LM-proposed narrative actions against ``state``,
        and return the ones the story accepted (what is recorded for the
        turn, so the log, the picture book and the history agree with the
        story, not with what was merely proposed).

        The base implementation keeps them all and re-arms events; a
        scenario with its own action vocabulary overrides this."""
        self._arm_required_events()
        return list(actions)

    def observe_settled_details(self, details: list[SettledDetail | dict]) -> None:
        """Hook: record what each turn settled against ``state``.

        The base implementation does nothing but re-arm events; a scenario
        that tracks settled details overrides this."""
        self._arm_required_events()

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
        self._playback_deadlines.pop(due.event_id, None)
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
        """Hook: render controller-owned state into the public narrative
        state once, regardless of what any individual Example's own
        situation_update did or forgot to do. The base implementation is the
        identity; a scenario with state to render overrides this."""
        return situation

    def fixed_line_for(self, classification: Any) -> str | None:
        """Hook: a scenario-specific fixed line for this turn's classify-stage
        result (whatever extra fields that scenario's own AnalyzeInteraction
        subclass adds, e.g. eeyore_birthday_events.EeyoreAnalyzeInteraction's
        honey_requests), bypassing generation entirely, or None to generate
        normally. The base implementation never has one; a scenario whose
        classify stage can short-circuit generation overrides this. ``None``
        when nothing was classified this turn (a world event)."""
        return None

    def response_may_be_replaced(self, user_action: str, history: str) -> bool:
        """Hook: whether NarrativeSession.submit() may still swap the
        generated line once it is complete, so it must be held back instead
        of streamed to the robot as it is written. The base implementation
        never replaces a line, so nothing is ever held back; a scenario whose
        enforce_response_invariants can replace a line overrides this,
        judging from the same input the eventual replacement would."""
        return False

    def enforce_response_invariants(
        self, user_action: str, history: str, bot_response: str,
        narrative_actions: list[str] | tuple[str, ...] = (),
    ) -> str:
        """Hook: apply small deterministic guards for requirements the LM
        output may violate, returning a replacement line (or bot_response
        unchanged). ``narrative_actions`` are what interpret() reported for
        the generated line, so a guard can rely on its judgment instead of
        matching keywords (see footprints_events). The base implementation never replaces anything; a
        scenario with such a guard overrides this (see
        eeyore_birthday_events.HoneyGiftEventController for the worked
        example)."""
        return bot_response

    def extra_actions_for(self, bot_response: str) -> list[str]:
        """Hook: narrative_actions to record as if the model had reported
        them, when enforce_response_invariants replaced its line with one
        that implies an action the model's own (discarded) line might not
        have included. The base implementation never adds any."""
        return []
