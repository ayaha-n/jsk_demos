"""Technical expressions and their human-reviewed story-world mishearings."""

from dataclasses import dataclass

from pydantic import BaseModel, Field


class MishearingCandidate(BaseModel):
    original_term: str = Field(description="参加者が用いた技術語")
    heard_as: str = Field(description="音の近い物語世界内の語")
    narrative_link: str = Field(description="現在の物語場面との接続先")
    possible_response: str = Field(description="プーが利用できる短い応答例")


@dataclass(frozen=True)
class MishearingCase:
    technical_expression: str
    misheard_word: str
    story_connection: str
    possible_response: str


MISHEARING_CASES = (
    MishearingCase(
        technical_expression="ロボット",
        misheard_word="ロバ",
        story_connection="ロバのイーヨー",
        possible_response="ロバ？ イーヨーのことかなあ。",
    ),
    MishearingCase(
        technical_expression="モーター",
        misheard_word="もう食べた",
        story_connection="プーが蜂蜜をすでに少し食べてしまったこと",
        possible_response="もう食べた？ じつは蜂蜜を少し食べちゃったんだ。",
    ),
    MishearingCase(
        technical_expression="アクチュエータ",
        misheard_word="握手",
        story_connection="参加者との握手",
        possible_response="握手？ ぼくもきみと握手したいな。",
    ),
)


def describe_mishearing(technical_expression: str) -> str:
    """Return the internal reframing plan for a reviewed expression."""
    for case in MISHEARING_CASES:
        if case.technical_expression == technical_expression:
            return (
                f"『{case.technical_expression}』を"
                f"音の近い『{case.misheard_word}』と聞き違え、"
                f"{case.story_connection}へ接続する。"
            )
    raise KeyError(f"未登録の聞き違い例です: {technical_expression}")


def candidate_for(technical_expression: str) -> MishearingCandidate:
    """Return one reviewed candidate for use in examples."""
    for case in MISHEARING_CASES:
        if case.technical_expression == technical_expression:
            return MishearingCandidate(
                original_term=case.technical_expression,
                heard_as=case.misheard_word,
                narrative_link=case.story_connection,
                possible_response=case.possible_response,
            )
    raise KeyError(f"未登録の聞き違い例です: {technical_expression}")


def known_candidates_for(
    technical_terms: list[str],
    history: str = "",
) -> tuple[list[MishearingCandidate], list[str]]:
    """Return reviewed candidates and terms that still need generation."""
    reviewed: list[MishearingCandidate] = []
    unknown: list[str] = []
    for technical_term in technical_terms:
        matched = next(
            (
                case
                for case in MISHEARING_CASES
                if technical_term in {
                    case.technical_expression,
                    *(
                        case.technical_expression[:index]
                        + case.technical_expression[index + 1 :]
                        for index, character in enumerate(case.technical_expression)
                        if character == "ー"
                    ),
                }
            ),
            None,
        )
        if matched is None:
            unknown.append(technical_term)
        elif matched.misheard_word not in history:
            reviewed.append(candidate_for(matched.technical_expression))
    return reviewed, unknown


def find_known_technical_terms(user_action: str) -> list[str]:
    """Find reviewed terms, accepting a missing long-vowel mark."""
    found = []
    for case in MISHEARING_CASES:
        term = case.technical_expression
        variants = {term}
        variants.update(
            term[:index] + term[index + 1 :]
            for index, character in enumerate(term)
            if character == "ー"
        )
        if any(variant and variant in user_action for variant in variants):
            found.append(case.technical_expression)
    return found
