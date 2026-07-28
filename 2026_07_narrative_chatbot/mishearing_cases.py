"""Technical expressions and their human-reviewed story-world mishearings."""

from dataclasses import dataclass
import unicodedata

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
    MishearingCase(
        technical_expression="機械",
        misheard_word="木かい",
        story_connection="100エーカーの森の木",
        possible_response="木かい？ 100エーカーの森には、木がたくさんあるよ。",
    ),
    MishearingCase(
        technical_expression="関節",
        misheard_word="季節",
        story_connection="蜂蜜がおいしい季節",
        possible_response="季節？ 今は、蜂蜜がおいしい季節かな。",
    ),
    MishearingCase(
        technical_expression="部品",
        misheard_word="プディング",
        story_connection="お茶会のおやつ",
        possible_response="プディング？ それ、おやつの名前？",
    ),
    MishearingCase(
        technical_expression="ねじ",
        misheard_word="虹",
        story_connection="雨上がりの虹",
        possible_response="虹？ 雨が降ったあとに出るものだね。",
    ),
    MishearingCase(
        technical_expression="触覚",
        misheard_word="収穫",
        story_connection="蜂蜜の収穫",
        possible_response="収穫？ 蜂蜜を収穫するの？",
    ),
    MishearingCase(
        technical_expression="反応",
        misheard_word="パン",
        story_connection="蜂蜜を塗ったパン",
        possible_response="パン？ パンには、蜂蜜を塗るとおいしいよ。",
    ),
    MishearingCase(
        technical_expression="入力",
        misheard_word="入浴",
        story_connection="お風呂",
        possible_response="入浴？ お風呂に入る話？",
    ),
    MishearingCase(
        technical_expression="カメラ",
        misheard_word="カメ",
        story_connection="森のカメ",
        possible_response="カメ？ カメさんがいるの？",
    ),
    MishearingCase(
        technical_expression="発話",
        misheard_word="葉っぱ",
        story_connection="森の葉っぱ",
        possible_response="葉っぱ？ 葉っぱがどうしたの？",
    ),
    MishearingCase(
        technical_expression="マイク",
        misheard_word="マーク",
        story_connection="印をつけること",
        possible_response="マーク？ どこかに印をつけるの？",
    ),
    MishearingCase(
        technical_expression="スピーカー",
        misheard_word="好きか",
        story_connection="プーが蜂蜜を好きなこと",
        possible_response="好きかって聞いた？ 蜂蜜は、とっても好きだよ。",
    ),
    MishearingCase(
        technical_expression="ノイズ",
        misheard_word="野いちご",
        story_connection="森で摘む野いちご",
        possible_response="野いちご？ 森で摘めるかな。",
    ),
    MishearingCase(
        technical_expression="バッテリー",
        misheard_word="ばったり",
        story_connection="誰かが倒れた可能性",
        possible_response="ばったり？ 誰かが倒れちゃったの？",
    ),
    MishearingCase(
        technical_expression="コマンド",
        misheard_word="困った",
        story_connection="参加者と一緒に考えること",
        possible_response="困った？ 困っているなら、いっしょに考えよう。",
    ),
    MishearingCase(
        technical_expression="Wi-Fi",
        misheard_word="わーい",
        story_connection="うれしい出来事",
        possible_response="わーい？ 何かうれしいことがあったの？",
    ),
    MishearingCase(
        technical_expression="実験",
        misheard_word="事件",
        story_connection="森で起きた事件",
        possible_response="事件？ 森で何か事件が起きたの？",
    ),
    MishearingCase(
        technical_expression="研究",
        misheard_word="ケーキ",
        story_connection="お茶会のケーキ",
        possible_response="ケーキ？ お茶会にケーキがあるの？",
    ),
    MishearingCase(
        technical_expression="データ",
        misheard_word="出た",
        story_connection="何かが見つかったこと",
        possible_response="出た？ 何か見つかったの？",
    ),
    MishearingCase(
        technical_expression="実装",
        misheard_word="じっと座る",
        story_connection="その場にじっと座ること",
        possible_response="じっとって言った？ じっと座っていればいい？",
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


def normalize_term(text: str) -> str:
    """Normalize width, case, and katakana/hiragana differences."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        chr(ord(character) - 0x60) if "ァ" <= character <= "ヶ" else character
        for character in normalized
    )


def term_variants(term: str) -> set[str]:
    """Return normalized variants, including omitted long-vowel marks."""
    normalized = normalize_term(term)
    return {
        normalized,
        *(
            normalized[:index] + normalized[index + 1 :]
            for index, character in enumerate(normalized)
            if character == "ー"
        ),
    }


def contains_technical_term(text: str, technical_terms: list[str]) -> bool:
    """Return whether text directly contains any supplied technical term."""
    normalized_text = normalize_term(text)
    return any(
        variant and variant in normalized_text
        for term in technical_terms
        for variant in term_variants(term)
    )


def known_candidates_for(
    technical_terms: list[str],
    history: str = "",
) -> tuple[list[MishearingCandidate], list[str]]:
    """Return reviewed candidates and terms that still need generation."""
    reviewed: list[MishearingCandidate] = []
    unknown: list[str] = []
    for technical_term in technical_terms:
        normalized_term = normalize_term(technical_term)
        matched = next(
            (
                case
                for case in MISHEARING_CASES
                if normalized_term in term_variants(case.technical_expression)
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
    normalized_action = normalize_term(user_action)
    for case in MISHEARING_CASES:
        if any(
            variant and variant in normalized_action
            for variant in term_variants(case.technical_expression)
        ):
            found.append(case.technical_expression)
    return found
