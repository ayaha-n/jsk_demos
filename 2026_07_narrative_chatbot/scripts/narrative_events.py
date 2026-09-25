"""Deterministic state and scheduling for timed narrative events."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from narrative_state import NarrativeSituation, SituationUpdate, apply_situation_update


NarrativeAction = Literal[
    "commit_honey_jar_gift",
    "block_pooh_honey_access",
    "resolve_empty_jar_gift",
    "resolve_balloon_color",
    "resolve_ribbon_color",
]

KNOWN_NARRATIVE_ACTIONS = {
    "commit_honey_jar_gift",
    "block_pooh_honey_access",
    "resolve_empty_jar_gift",
    "resolve_balloon_color",
    "resolve_ribbon_color",
}

HONEY_EATEN_RESPONSE = (
    "いやんなっちゃう！味見してたら、はちみつ、なくなっちゃった。どうしよう？"
    "だってぼく、なにかやらなくちゃならないもの。"
)
HONEY_EATEN_DESCRIPTION = (
    "イーヨーへの贈り物にすると決めた蜂蜜を、待っている間に"
    "プーが全部食べてしまい、壺が空になった。これは既に起きた出来事。"
)
HONEY_GIFT_COMMITTED_DESCRIPTION = (
    "プーがイーヨーに蜂蜜の入った壺を贈ることに決めた。これは既に起きた出来事。"
)
HONEY_GIFT_COMMITTED_EVENT = "プーがハチミツの入った壺をイーヨーに贈ることに決めた"
HONEY_GIFT_COMMITTED_RESPONSE = (
    "そうだ！ぼくは、イーヨーにハチミツの壺を贈ることにしよう。きっと喜ぶね。"
)
HONEY_GIFT_COMMITTED_FOLLOW_UP_RESPONSE = (
    "あ、そうだ！ぼくは、イーヨーにハチミツの壺を贈ることにしよう。きっと喜ぶね。"
)
HONEY_TASTED_EVENT = "プーがハチミツを一口だけのつもりで持ち出した"
HONEY_TASTED_DESCRIPTION = (
    "イーヨーへの贈り物にすると決めた蜂蜜を、プーが待っている間に一口だけの"
    "つもりで持ち出した。まだ食べ切ってはいない。これは既に起きた出来事。"
)
HONEY_TASTED_RESPONSE = (
    "みつのツボを持ってるなんて、運が良かったなあ。ちょっと一口やるものを持ってるなんて。"
    "…さあて、ぼくはなにをするんだっけ？"
)
HONEY_TASTED_FOLLOW_UP_RESPONSE = f"あ、そういえば。{HONEY_TASTED_RESPONSE}"
GIFT_DECISION_UNRESOLVED = (
    "イーヨーに何をあげるか",
    "プレゼントの準備がまだできていない",
)
HONEY_PREPARATION_UNRESOLVED = "ハチミツの準備をどう進めるか"
EMPTY_JAR_UNRESOLVED = "空になった壺をどうするか"
BALLOON_COLOR_UNRESOLVED = "贈り物にする風船の色"
RIBBON_COLOR_UNRESOLVED = "リボンの色"
BLOCKED_ACCESS_EVENT = "参加者が贈り物の蜂蜜を食べないよう明確に制止した"


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


@dataclass
class HoneyGiftState:
    gift_status: str = "undecided"
    honey_status: str = "full"
    access_restriction: str = "none"
    completed_event_ids: set[str] = field(default_factory=set)


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
    ) -> None:
        if gift_decision_delay_seconds < 0:
            raise ValueError("gift_decision_delay_seconds must be non-negative")
        if tasting_delay_seconds < 0:
            raise ValueError("tasting_delay_seconds must be non-negative")
        if eating_delay_seconds < 0:
            raise ValueError("eating_delay_seconds must be non-negative")
        if quiet_seconds < 0:
            raise ValueError("quiet_seconds must be non-negative")
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
        )
        self._deadlines: dict[str, float] = {}
        self._last_activity = clock()
        self._arm_required_events()

    @staticmethod
    def _default_required_events(
        gift_decision_delay_seconds: float,
        tasting_delay_seconds: float,
        eating_delay_seconds: float,
    ) -> tuple[RequiredNarrativeEvent, ...]:
        return (
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
        )

    def _arm_required_events(self) -> None:
        now = self.clock()
        for event in self.required_events:
            if (
                event.event_id not in self.state.completed_event_ids
                and event.prerequisite(self.state)
                and event.event_id not in self._deadlines
            ):
                self._deadlines[event.event_id] = now + event.delay_seconds

    def observe_activity(self) -> None:
        """Record participant input or delivered Pooh output; deadlines never move."""
        self._last_activity = self.clock()

    def _ready_at(self, event: RequiredNarrativeEvent, follow_up: bool) -> float:
        deadline = self._deadlines[event.event_id]
        if event.waits_for_quiet and not follow_up:
            return max(deadline, self._last_activity + self.quiet_seconds)
        return deadline

    def observe_actions(self, actions: list[str]) -> None:
        """Validate and apply LM-proposed actions without resetting equal decisions."""
        for action in actions:
            if action not in KNOWN_NARRATIVE_ACTIONS:
                continue
            if action == "commit_honey_jar_gift":
                self._commit_gift()
            elif action == "block_pooh_honey_access":
                self.state.access_restriction = "blocked"
                self._cancel_schedule()
            elif action == "resolve_empty_jar_gift":
                self._resolve_empty_jar_gift()
            elif action == "resolve_balloon_color":
                self._resolve_balloon_color()
            elif action == "resolve_ribbon_color":
                self._resolve_ribbon_color()
        self._arm_required_events()

    def _resolve_empty_jar_gift(self) -> None:
        # Only meaningful once the jar is actually empty; a premature signal
        # must not silently pre-clear the unresolved item before it even
        # exists, which would hide the "空になった壺をどうするか" thread
        # entirely once the honey is later eaten.
        if self.state.honey_status != "empty":
            return
        self.state.completed_event_ids.add("empty_jar_gift_resolved")

    def _resolve_balloon_color(self) -> None:
        # Whoever proposed the color (Pooh's own guess or the participant's
        # own answer) is DSPy's call to make; Python only records that the
        # color topic is settled, so the "贈り物にする風船の色" unresolved
        # item is cleared deterministically regardless of phrasing.
        self.state.completed_event_ids.add("balloon_color_resolved")

    def _resolve_ribbon_color(self) -> None:
        # Same design as _resolve_balloon_color: Python only tracks that the
        # ribbon-color topic is settled, never the chosen value itself.
        self.state.completed_event_ids.add("ribbon_color_resolved")

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
        if "empty_jar_gift_resolved" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(remove_unresolved=[EMPTY_JAR_UNRESOLVED]),
            )
        if "balloon_color_resolved" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(remove_unresolved=[BALLOON_COLOR_UNRESOLVED]),
            )
        if "ribbon_color_resolved" in self.state.completed_event_ids:
            situation = apply_situation_update(
                situation,
                SituationUpdate(remove_unresolved=[RIBBON_COLOR_UNRESOLVED]),
            )
        if self.state.access_restriction == "blocked":
            situation = apply_situation_update(
                situation,
                SituationUpdate(add_events=[BLOCKED_ACCESS_EVENT]),
            )
        return situation
