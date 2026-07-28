"""Technical expressions and their human-reviewed story-world mishearings."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MishearingCase:
    technical_expression: str
    misheard_word: str
    story_connection: str


MISHEARING_CASES = (
    MishearingCase(
        technical_expression="ロボット",
        misheard_word="ロバ",
        story_connection="ロバのイーヨー",
    ),
    MishearingCase(
        technical_expression="モーター",
        misheard_word="もう食べた",
        story_connection="プーが蜂蜜をすでに少し食べてしまったこと",
    ),
    MishearingCase(
        technical_expression="アクチュエータ",
        misheard_word="握手",
        story_connection="参加者との握手",
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
