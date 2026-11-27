"""English mode: a participant speaking English with the Japanese program.

The compiled DSPy program stays Japanese and unchanged. In English mode the
participant's English is translated into Japanese before the program sees
it, and Pooh's Japanese line is translated into English before it is shown
or spoken. Everything kept inside the session (history, story state, the
event controllers' checks, the log's ``user_action`` and ``bot_response``)
stays Japanese, so the scenarios work as they do in Japanese.

Mishearing is the one thing done in English: it depends on how the words
sound, so a technical word is looked up in an English table, or given to an
English planner, on the participant's own English, before translation
(translated first, "robot" would become ロボット and be misheard as ロバ, a
Japanese pun that makes no sense once translated back). The English planner
and its examples are kept out of the compiled program and its cache hash, so
the Japanese compiled files load and are named as before.

Technical words stay in Latin letters through the translation into Japanese,
so the Japanese program extracts the English word; whatever Pooh says in
Latin letters (the misheard word, or a fragment echoed back) is kept as it
is in the translation into English.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import dspy

from mishearing_cases import MishearingCandidate, uncertain_candidate_for
from narrative_state import NarrativeSituation

LANGUAGES = ("ja", "en")
DEFAULT_LANGUAGE = "ja"
ENGLISH_TEXT_PATH = Path(__file__).resolve().parents[1] / "config" / "english.json"


@lru_cache(maxsize=1)
def english_text() -> dict[str, Any]:
    """config/english.json: the scenarios' titles and scene descriptions, and
    Pooh's fixed lines, in English."""
    return json.loads(ENGLISH_TEXT_PATH.read_text(encoding="utf-8"))


def scenario_text(scenario_key: str, field: str, default: str) -> str:
    """A scenario's ``title`` or ``scene_intro`` in English, or ``default``."""
    return english_text().get("scenarios", {}).get(scenario_key, {}).get(field) or default


@dataclass(frozen=True)
class EnglishMishearingCase:
    technical_expression: str
    misheard_word: str
    # In Japanese, as the program's own table: the response is written in
    # Japanese (with the misheard word in Latin letters) and translated.
    story_connection: str
    possible_response: str


# To be reviewed by a native speaker, as the Japanese table was. The misheard
# word sounds close in English and belongs to the story world.
ENGLISH_MISHEARING_CASES = (
    EnglishMishearingCase(
        technical_expression="robot",
        misheard_word="Rabbit",
        story_connection="森に住むラビット",
        possible_response="Rabbit？ ラビットも、お祝いに呼ぼうか。",
    ),
    EnglishMishearingCase(
        technical_expression="camera",
        misheard_word="Kanga",
        story_connection="森に住むカンガ",
        possible_response="Kanga？ カンガなら、きっと手伝ってくれるよ。",
    ),
    EnglishMishearingCase(
        technical_expression="battery",
        misheard_word="buttery",
        story_connection="バターをぬったおやつ",
        possible_response="buttery？ バターとハチミツのパンは、おいしいよね。",
    ),
    EnglishMishearingCase(
        technical_expression="algorithm",
        misheard_word="a rhythm",
        story_connection="プーが口ずさむ歌",
        possible_response="a rhythm？ ぼく、ハミングするのは好きだよ。",
    ),
    EnglishMishearingCase(
        technical_expression="computer",
        misheard_word="come to tea",
        story_connection="お茶会への招待",
        possible_response="come to tea？ お茶会、いいねえ。",
    ),
    EnglishMishearingCase(
        technical_expression="sensor",
        misheard_word="center",
        story_connection="あき地のまんなか",
        possible_response="center？ あき地のまんなかに、なにかあるの？",
    ),
    EnglishMishearingCase(
        technical_expression="software",
        misheard_word="soft wear",
        story_connection="やわらかいマフラー",
        possible_response="soft wear？ やわらかいマフラーなら、イーヨーもあったかいね。",
    ),
    EnglishMishearingCase(
        technical_expression="AI",
        misheard_word="eye",
        story_connection="目をこらして見ること",
        possible_response="eye？ 目をこらしても、ぼくにはよく見えないなあ。",
    ),
)


def _english_term_pattern(term: str) -> re.Pattern[str]:
    # Whole words only ("AI" is not in "said"); a plural is the same word.
    return re.compile(rf"(?<![A-Za-z]){re.escape(term)}(?:s|es)?(?![A-Za-z])", re.IGNORECASE)


def _english_case_for(term: str) -> EnglishMishearingCase | None:
    word = term.strip()
    return next(
        (case for case in ENGLISH_MISHEARING_CASES
         if _english_term_pattern(case.technical_expression).fullmatch(word)),
        None,
    )


def english_candidate_for(technical_expression: str) -> MishearingCandidate:
    case = _english_case_for(technical_expression)
    if case is None:
        raise KeyError(f"未登録の英語の聞き違い例です: {technical_expression}")
    return MishearingCandidate(
        original_term=case.technical_expression,
        heard_as=case.misheard_word,
        narrative_link=case.story_connection,
        possible_response=case.possible_response,
    )


def find_english_technical_terms(text: str) -> list[str]:
    """The table's terms in the participant's English."""
    return [case.technical_expression for case in ENGLISH_MISHEARING_CASES
            if _english_term_pattern(case.technical_expression).search(text)]


def english_known_candidates(
    technical_terms: list[str], history: str = ""
) -> tuple[list[MishearingCandidate], list[str]]:
    """As mishearing_cases.known_candidates_for, with the English table."""
    reviewed: list[MishearingCandidate] = []
    unknown: list[str] = []
    for term in technical_terms:
        case = _english_case_for(term)
        if case is None:
            unknown.append(term)
        elif case.misheard_word not in history:
            reviewed.append(english_candidate_for(case.technical_expression))
    return reviewed, unknown


class PlanEnglishMishearing(dspy.Signature):
    """For each technical English word the participant used, propose words from
    Pooh's story world that SOUND close to it in English (e.g. robot -> Rabbit,
    battery -> buttery), so Pooh can mishear it and bring the talk back to the
    story. heard_as is the English word or phrase in Latin letters.
    narrative_link and possible_response are in Japanese (Pooh's line is written
    in Japanese and translated into English later); possible_response begins
    with heard_as in Latin letters, e.g. "buttery？ バターのパンは、おいしいよね。".
    Do not repeat a mishearing already in the history; with no natural
    candidate, return an empty list.
    """

    current_situation: NarrativeSituation = dspy.InputField()
    user_utterance: str = dspy.InputField(desc="The participant's own English.")
    technical_terms: list[str] = dspy.InputField()
    history: str = dspy.InputField()
    candidates: list[MishearingCandidate] = dspy.OutputField(desc="0 to 3 natural candidates.")


def english_mishearing_examples() -> list[dspy.Example]:
    from pooh_examples import INITIAL_SITUATION

    return [
        dspy.Example(
            current_situation=INITIAL_SITUATION,
            user_utterance=utterance,
            history="",
            technical_terms=[term],
            candidates=[english_candidate_for(term)],
        ).with_inputs("current_situation", "user_utterance", "technical_terms", "history")
        for utterance, term in (
            ("You're a robot, aren't you?", "robot"),
            ("Where is your battery?", "battery"),
            ("Is there a camera in your eyes?", "camera"),
        )
    ]


class EnglishMishearing:
    """The mishearing hook for PoohNarrativeAgent.forward in English mode:
    terms are found in, and candidates planned for, ``user_text`` (the
    participant's English), with the English table and planner."""

    def __init__(self, user_text: str, planner: Any) -> None:
        self.user_text = user_text
        self._planner = planner

    def find_terms(self, _translated: str) -> list[str]:
        return find_english_technical_terms(self.user_text)

    @staticmethod
    def own_terms(extracted: list[str]) -> list[str]:
        """Of the terms the classifier found in the translation, those still
        in Latin letters: technical words are kept in English through the
        translation, so one found in Japanese was an ordinary word translated
        ("research" -> 研究), which the English planner would mishear as a
        Japanese sound ("Ken?")."""
        return [term for term in extracted if re.search(r"[A-Za-z]", term)]

    def known_candidates(self, technical_terms: list[str], history: str = ""):
        return english_known_candidates(technical_terms, history)

    def plan(self, **inputs: Any) -> Any:
        return self._planner(**{**inputs, "user_utterance": self.user_text})

    @staticmethod
    def uncertain(term: str) -> MishearingCandidate:
        return uncertain_candidate_for(term)


class TranslateParticipantToJapanese(dspy.Signature):
    """Translate what the English-speaking participant said to Pooh (or the
    action they described) into natural, plain spoken Japanese, as said to
    Pooh in a Japanese conversation. Keep technical, computer and
    modern-technology words (robot, AI, sensor, battery, motor, ...) and
    anything you cannot translate in English, in Latin letters, exactly as
    written. Keep names of the story's characters as their usual Japanese
    names (Eeyore -> イーヨー, Pooh -> プー). Output only the translation."""

    english: str = dspy.InputField()
    pooh_said_before: str = dspy.InputField(desc="Pooh's line just before, in Japanese, for context.")
    japanese: str = dspy.OutputField()


class TranslateObservedAction(dspy.Signature):
    """Put what the robot saw the participant do, written in Japanese as
    「参加者が…」, into a short English verb phrase in the past tense without
    its subject, as a picture book tells the reader what they did ("You
    ..."). Plain words a child understands; Pooh stays Pooh (例:
    参加者がぬいぐるみをプーに見せた -> showed Pooh a stuffed toy). Output only
    the phrase."""

    japanese: str = dspy.InputField()
    english: str = dspy.OutputField()


class TranslatePoohToEnglish(dspy.Signature):
    """Translate Pooh's Japanese line into English, as Pooh says it to an
    English-speaking participant. Pooh is a gentle, simple, warm little
    bear: short sentences and plain words. Keep the meaning, any question,
    and the length; add nothing. Keep every word written in Latin letters
    exactly as it is (it is a word Pooh misheard, or echoes back): do not
    correct or translate it. Only words already in Latin letters in the
    Japanese stay that way: katakana words are ordinary Japanese words, so
    translate them too, never write them out in Latin letters (ハチミツ,
    はちみつ, みつ -> honey; ツボ, 壺, つぼ -> pot). Characters' names in their
    usual English form (イーヨー -> Eeyore; あやは, who made Pooh,
    -> Ayaha). Output only the translation."""

    japanese: str = dspy.InputField()
    english: str = dspy.OutputField()


# Japanese words the translation into English has been seen to write out in
# Latin letters instead of translating ("a pot of hachimitsu", 2026-11-24),
# and their English. Replaced only when the Japanese line did not have the
# word in Latin letters itself (then it is a word Pooh misheard, kept as is).
ROMANIZED_WORDS = {
    "hachimitsu": "honey",
    "hachi-mitsu": "honey",
    "mitsu": "honey",
    "tsubo": "pot",
}


def replace_romanized_words(english: str, japanese: str) -> str:
    """``english`` with ROMANIZED_WORDS translated, keeping a capital."""
    for word, translation in ROMANIZED_WORDS.items():
        # Not \b: in "mitsuってなあに" the kana count as word characters too.
        pattern = rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])"
        if re.search(pattern, japanese, flags=re.IGNORECASE):
            continue

        def translated(match: re.Match) -> str:
            return translation.capitalize() if match.group(0)[0].isupper() else translation

        english = re.sub(pattern, translated, english, flags=re.IGNORECASE)
    return english


class Translator:
    """Translation for English mode, by the configured LM. Pooh's fixed lines
    repeat (the opening, the ending, timed events), so translations into
    English are kept and reused; those in config/english.json are not
    translated at all."""

    def __init__(self, to_japanese: Any = None, to_english: Any = None,
                 lines: dict[str, str] | None = None, observed: Any = None) -> None:
        self._to_japanese = to_japanese or dspy.Predict(TranslateParticipantToJapanese)
        self._to_english = to_english or dspy.Predict(TranslatePoohToEnglish)
        self._observed = observed or dspy.Predict(TranslateObservedAction)
        # The fixed lines written in English (config/english.json) are said
        # as written.
        self._cache: dict[str, str] = dict(
            english_text().get("lines", {}) if lines is None else lines)
        self._lock = threading.Lock()

    def observed_to_english(self, action: str) -> str:
        """What the robot saw (「参加者がぬいぐるみをプーに見せた」) as an
        English phrase for the picture book ("showed Pooh a stuffed toy"),
        or "" if it could not be translated."""
        key = "observed:" + action
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            english = str(self._observed(japanese=action).english).strip().rstrip(".")
        except Exception as exc:
            print(f"翻訳できませんでした: {type(exc).__name__}: {exc}", file=sys.stderr)
            return ""
        with self._lock:
            self._cache[key] = english
        return english

    def to_japanese(self, english: str, pooh_said_before: str = "") -> str:
        result = self._to_japanese(english=english, pooh_said_before=pooh_said_before)
        return str(result.japanese).strip() or english

    def to_english(self, japanese: str) -> str:
        if not japanese:
            return japanese
        with self._lock:
            cached = self._cache.get(japanese)
        if cached is not None:
            return cached
        try:
            english = str(self._to_english(japanese=japanese).english).strip()
        except Exception as exc:
            print(f"翻訳できませんでした: {type(exc).__name__}: {exc}", file=sys.stderr)
            return japanese
        if not english:
            return japanese
        english = replace_romanized_words(english, japanese)
        with self._lock:
            self._cache[japanese] = english
        return english


def english_planner() -> Any:
    """The English mishearing planner, outside the compiled program, with the
    English examples as its demos."""
    planner = dspy.ChainOfThought(PlanEnglishMishearing)
    planner.demos = english_mishearing_examples()
    return planner
