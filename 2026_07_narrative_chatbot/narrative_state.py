"""Structured narrative state and deterministic copy-on-write updates."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NarrativeSituation(BaseModel):
    """Complete story state passed between interaction turns."""

    model_config = ConfigDict(extra="forbid")

    place: str
    purpose: str
    characters: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    relationship: str = ""
    unresolved: list[str] = Field(default_factory=list)

    def __str__(self) -> str:
        return format_situation(self)


class SituationUpdate(BaseModel):
    """Only the changes proposed by the response Predictor for one turn."""

    model_config = ConfigDict(extra="forbid")

    place: str | None = Field(default=None, description="新しい場所。変更しない場合はnull。")
    purpose: str | None = Field(default=None, description="新しい場面の目的。変更しない場合はnull。")
    add_characters: list[str] = Field(default_factory=list)
    remove_characters: list[str] = Field(default_factory=list)
    add_props: list[str] = Field(default_factory=list)
    remove_props: list[str] = Field(default_factory=list)
    add_events: list[str] = Field(default_factory=list)
    remove_events: list[str] = Field(default_factory=list)
    relationship: str | None = Field(default=None, description="新しい関係。変更しない場合はnull。")
    add_unresolved: list[str] = Field(default_factory=list)
    remove_unresolved: list[str] = Field(default_factory=list)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def coerce_situation(
    value: NarrativeSituation | dict[str, Any],
) -> NarrativeSituation:
    """Validate already-structured state without parsing display text."""
    if isinstance(value, NarrativeSituation):
        return value
    return NarrativeSituation.model_validate(value)


def format_situation(situation: NarrativeSituation) -> str:
    """Render structured state in a human-readable Japanese format."""
    def joined(values: list[str]) -> str:
        return "、".join(values) if values else "なし"

    return (
        f"【場所】{situation.place}。\n"
        f"【場面の目的】{situation.purpose}。\n"
        f"【登場人物】{joined(situation.characters)}。\n"
        f"【小道具と状態】{joined(situation.props)}。\n"
        f"【重要な出来事】{joined(situation.events)}。\n"
        f"【関係】{situation.relationship}。\n"
        f"【未解決・未確定】{joined(situation.unresolved)}。"
    )


def apply_situation_update(
    current: NarrativeSituation | dict[str, Any],
    update: SituationUpdate | dict[str, Any],
) -> NarrativeSituation:
    """Apply an LLM-proposed delta to a deep copy of the current state."""
    source = coerce_situation(current)
    change = SituationUpdate.model_validate(update)
    result = source.model_copy(deep=True)

    if change.place is not None:
        result.place = change.place
    if change.purpose is not None:
        result.purpose = change.purpose
    if change.relationship is not None:
        result.relationship = change.relationship

    for field, additions, removals in (
        ("characters", change.add_characters, change.remove_characters),
        ("props", change.add_props, change.remove_props),
        ("events", change.add_events, change.remove_events),
        ("unresolved", change.add_unresolved, change.remove_unresolved),
    ):
        values = [item for item in getattr(result, field) if item not in removals]
        setattr(result, field, _unique(values + additions))
    return result
