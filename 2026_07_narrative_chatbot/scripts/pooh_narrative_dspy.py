#!/usr/bin/env python3
"""DSPy prototype for open-ended narrative interaction with a Pooh character.

Python 3.12 / DSPy 3.2.1 are required.  The default ``chat`` command only
loads an already compiled program; use ``--mode compile`` explicitly to build
one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import select
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# DSPyのリクエストキャッシュもホームではなく、このプロジェクト内へ隔離する。
os.environ.setdefault(
    "DSPY_CACHEDIR",
    str(PROJECT_ROOT / ".dspy_cache" / "requests"),
)

try:
    import dspy
    from dspy.streaming import StreamListener, StreamResponse
    from dspy.teleprompt import BootstrapFewShot
except ImportError:
    print("DSPy がありません。Python 3.12環境で `pip install dspy==3.2.1` を実行してください。")
    raise SystemExit(1)


from mishearing_cases import (
    MishearingCandidate,
    contains_technical_term,
    find_known_technical_terms,
    known_candidates_for,
    uncertain_candidate_for,
)
from narrative_state import (
    NarrativeSituation,
    SituationUpdate,
    apply_situation_update,
    coerce_situation,
    relevant_preferences,
)
from narrative_events import (
    NarrativeEventController,
    SettledDetail,
    WorldEvent,
    estimate_speech_seconds,
    fixed_utterance_id,
    fixed_utterance_motion,
)
from narrative_signatures import (
    AnalyzeInteraction,
    DISALLOWED_QUESTION_KINDS,
    GeneratePoohResponse,
    InterpretTurn,
    PlanMishearing,
)
from pooh_examples import (
    INITIAL_SITUATION,
    MISHEARING_EXAMPLES,
    MODE_EXAMPLES,
    RESPONSE_EXAMPLES,
    TRAINSET,
)
from narrative_relay import NarrativeRelayPublisher
from scenarios import DEFAULT_SCENARIO, SCENARIOS, Scenario, get_scenario, selected_scenario_keys


PROGRAM_VERSION = "pooh-structured-state-v46"
METRIC_VERSION = "structured-state-judge-v20"
EXPECTED_DSPY_VERSION = "3.2.1"
# gpt-4o follows the questioning and agency guidance far better than
# gpt-4o-mini at similar latency; the judge stays on the cheaper model.
DEFAULT_MODEL = "openai/gpt-4o"
DEFAULT_JUDGE_MODEL = "openai/gpt-4o-mini"
LM_TIMEOUT_SECONDS = 20.0
LM_NUM_RETRIES = 1
LOG_DIR = Path(os.getenv("POOH_LOG_DIR", str(PROJECT_ROOT / "logs")))
CACHE_DIR = Path(os.getenv("POOH_CACHE_DIR", str(PROJECT_ROOT / ".dspy_cache")))
BOOTSTRAP_METRIC_THRESHOLD = 0.8
MAX_BOOTSTRAPPED_DEMOS = 4
MAX_LABELED_DEMOS = 0
MAX_TOTAL_DEMOS_PER_STAGE = 12


# AnalyzeInteraction/PlanMishearing/GeneratePoohResponse/QuestionKind/
# InterpretTurn/_signature_field live in narrative_signatures.py (imported
# above); each scenario's own subclasses (e.g. eeyore_birthday_events's
# EeyoreAnalyzeInteraction/EeyoreInterpretTurn) live next to that scenario's
# other content and are wired in via its own Scenario.classify_signature/
# interpret_signature/response_signature fields (scenarios.py) — this module
# never imports a scenario's own events file at all (see signatures_for below
# and docs/known_issues.md).


class MetaPolicyEvaluator(dspy.Signature):
    """メタ入力への候補応答が、物語世界へ接続する方針を守るか評価する。

    技術語を復唱・説明する応答（例：「僕はロボットだけど...」）や、技術概念を理解した上で「ぼくはロボットではない」
    などと自己否定する応答は低く評価する。初回のメタ入力では、登録済みまたは生成した
    自然な物語世界内の聞き違いを優先する。自然な候補がない場合は、元の語から少なくとも
    1文字を削除または変更した1〜3文字の不確かな短い音も許容する。
    履歴に訂正や反復があれば、同じ聞き違いを繰り返さず、発話全体の曖昧な
    関心をプーの感覚、記憶、関心へ移した応答を高く評価する。参加者の技術的関心は
    内部状態に保持してよい。訂正後は技術語全体を復唱せず、1〜3文字の不確かな
    短い音と不理解を示す応答を高く評価する。「ああ、そのことね」のような理解表明、
    技術説明、自己同定は低く評価する。
    """

    user_action: str = dspy.InputField()
    history: str = dspy.InputField()
    reference_response: str = dspy.InputField(
        desc="人間が確認した方針例。表面一致ではなく設計意図の参照に使う。"
    )
    candidate_mode: str = dspy.InputField()
    candidate_response: str = dspy.InputField()
    policy_compliance: int = dspy.OutputField(
        desc=(
            "メタ応答方針への適合度。自然な候補があるのに使わない場合や、"
            "フォールバックの短い音が1〜3文字・最低1文字変更の条件を破る場合を含め、"
            "重大な違反があれば1〜3、十分に適合すれば4〜5。"
        )
    )
    rationale: str = dspy.OutputField(desc="違反または適合の根拠を簡潔に記す。")


class NarrativeQualityJudge(dspy.Signature):
    """候補が分類とモード別の設計原則を満たす度合いを1〜5で評価する。"""

    current_situation: NarrativeSituation = dspy.InputField()
    user_action: str = dspy.InputField()
    history: str = dspy.InputField()
    previous_bot_response: str = dspy.InputField(
        desc="直前のプー自身の応答。candidate_outputがこれと同じ、または意味的に同じ内容なら低く評価する。"
    )
    reference_output: str = dspy.InputField()
    candidate_output: str = dspy.InputField()
    mode_accuracy: int = dspy.OutputField(desc="interaction_modeの分類精度。1〜5。")
    response_fit: int = dspy.OutputField(
        desc="ordinaryを過剰に物語化せず、metaの聞き違い方針とexitを守るなど、モードへの適合度。1〜5。"
    )
    narrative_coherence: int = dspy.OutputField(
        desc=(
            "必要な場合の物語的一貫性。場面の進む順序が会話例と異なっても構わないが、"
            "まだ成立していない前提を成立したことにしていないかを含めて評価する。1〜5。"
        )
    )
    participant_agency: int = dspy.OutputField(desc="参加者の行為主体性。1〜5。")
    pooh_agency: int = dspy.OutputField(
        desc=(
            "プー自身が自分の好み・考え・次にしたいことを持ち、決定の主体を参加者へ"
            "委ねていないか。参加者の質問を答えずにそのまま聞き返していないか。"
            "迷いや不明への返答で、具体案を出さず同じ問いを言い換えて戻した場合は3以下。"
            "知らない事実を捏造せず、プー自身の案を理由とともに示す応答を高く評価する。"
            "参加者自身の考えや行動を初めて尋ねることは減点しない。1〜5。"
        )
    )
    conversational_progress: int = dspy.OutputField(
        desc=(
            "previous_bot_responseと今回の入力の関係。相づちには短い受け止め、"
            "またはプー自身の小さな次の行動で自然につなげれば高評価。場面を無理に"
            "進める必要はない。聞き直し・確認への必要な再提示は許容するが、"
            "要求されていない同じ説明・安心させるセリフの反復や、意味を変えない"
            "言い換えは3以下。previous_bot_responseと同じ・意味的に同じ内容の"
            "繰り返しは1。1〜5。"
        )
    )
    state_quality: int = dspy.OutputField(desc="状態更新の自足性と保持品質。1〜5。")
    rationale: str = dspy.OutputField(desc="評定根拠を簡潔に記す。")


def count_prior_technical_mentions(history: str, technical_terms: list[str]) -> int:
    """Count prior user turns that mention the current technical terms."""
    return sum(
        1
        for line in history.splitlines()
        if line.startswith("参加者の生入力:")
        and contains_technical_term(line, technical_terms)
    )


# A sentence ends at 。！？ (or a run of them, with any closing bracket);
# the line is handed on sentence by sentence as it streams in.
SENTENCE_END = re.compile(r"[。！？!?…]+[」』）)]*")


class SentenceFeed:
    """Hands on the sentences of a line as its text streams in.

    ``send`` gets each sentence once it is complete, then whatever of the
    final line is left; together they make the line.  An end mark only counts
    once more text follows, as the next chunk may extend it (「！」 to 「！？」).
    """

    def __init__(self, send: Callable[[str], None]) -> None:
        self.send = send
        self.text = ""
        self.sent = 0

    def feed(self, chunk: str) -> None:
        self.text += chunk
        for match in SENTENCE_END.finditer(self.text, self.sent):
            if match.end() < len(self.text):
                self.send(self.text[self.sent:match.end()])
                self.sent = match.end()

    def finish(self, line: str) -> bool:
        """Send the rest of the final line; False if it does not continue
        what was already sent (then nothing more is sent)."""
        already = self.text[:self.sent]
        if not line.startswith(already):
            return False
        if line[len(already):]:
            self.send(line[len(already):])
        return True


def stream_response(predictor: Any, on_text: Callable[[str], None], **inputs: Any) -> Any:
    """Call the respond stage, passing on bot_response as it is written.

    A predictor that is not a DSPy module (a test double) is just called.
    A cached answer comes back whole, without any streamed text.
    When streaming ends without an answer (its producer thread failed and
    printed its traceback), the stage is called again without streaming; a
    line that does not continue what was sent is not sent (SentenceFeed).
    """
    if not isinstance(predictor, dspy.Module):
        return predictor(**inputs)
    # A listener keeps its state after one line, so each call gets its own.
    streaming = dspy.streamify(
        predictor,
        stream_listeners=[StreamListener(signature_field_name="bot_response")],
        async_streaming=False,
    )
    result = None
    for item in streaming(**inputs):
        if isinstance(item, StreamResponse):
            on_text(item.chunk)
        elif isinstance(item, dspy.Prediction):
            result = item
    if result is None:
        print("Warning: streaming returned no response; generating it again",
              file=sys.stderr)
        result = predictor(**inputs)
    return result


def warn_if_response_repeated(bot_response: str, previous_bot_response: str | None) -> None:
    """Flag an exact repeat of the immediately preceding Pooh line.

    Detection only. A good non-repetitive line is context-dependent, so
    fixing it belongs to DSPy's examples/metric, not a deterministic
    Python substitution.
    """
    if previous_bot_response is not None and bot_response == previous_bot_response:
        print(
            f"警告: 直前の応答と完全に一致しています: {bot_response!r}",
            file=sys.stderr,
        )


# Which Signature subclass (if any) a scenario's classify/interpret stages
# use for their own closed vocabulary, keyed by scenario.key. A scenario
# without an entry here uses the plain, vocabulary-free base Signatures.
# Kept here, not on Scenario itself, since Scenario (scenarios.py) is loaded
# by this module and so cannot import these classes back from it.
def signatures_for(
    scenario: Scenario,
) -> tuple[type[dspy.Signature], type[dspy.Signature], type[dspy.Signature]]:
    """A scenario's own classify/interpret/response Signature subclasses
    (scenarios.Scenario.classify_signature/interpret_signature/
    response_signature), or the generic ones for whichever a scenario leaves
    as None. Scenario-specific subclasses are defined in that scenario's own
    events file (e.g. eeyore_birthday_events.py), never here — see
    docs/known_issues.md."""
    return (
        scenario.classify_signature or AnalyzeInteraction,
        scenario.interpret_signature or InterpretTurn,
        scenario.response_signature or GeneratePoohResponse,
    )


class PoohNarrativeAgent(dspy.Module):
    """Compiled multi-stage agent; the planner runs only for meta input."""

    def __init__(
        self,
        predictor_factory: Any = dspy.ChainOfThought,
        classify_signature: type[dspy.Signature] = AnalyzeInteraction,
        interpret_signature: type[dspy.Signature] = InterpretTurn,
        response_signature: type[dspy.Signature] = GeneratePoohResponse,
    ) -> None:
        super().__init__()
        self.classify = predictor_factory(classify_signature)
        self.plan = predictor_factory(PlanMishearing)
        # The line is written without a reasoning field in front of it, so it
        # reaches the robot sooner; the other stages keep their reasoning.
        respond_factory = (
            dspy.Predict if predictor_factory is dspy.ChainOfThought else predictor_factory
        )
        self.respond = respond_factory(response_signature)
        # Structured decisions are judged separately from writing the line, so
        # this stage's demos are all about decisions and it reads the final text.
        self.interpret = predictor_factory(interpret_signature)

    def forward(
        self,
        current_situation: NarrativeSituation | dict[str, Any],
        user_action: str,
        history: str,
        world_event: str = "",
        event_scene: str = "none",
        previous_bot_response: str = "",
        pooh_preferences: str = "",
        on_response: Callable[[str], None] | None = None,
        fixed_line_for: Callable[[Any], str | None] | None = None,
    ) -> Any:
        """``on_response(text)``, if given, receives the line before interpret,
        so the robot can start speaking: sentence by sentence while the line
        is written, then the rest of it.  A meta turn's line may be replaced
        by the technical-term guard, so it is passed whole once checked; an
        exit's line is replaced by the ending, so it is not passed.

        ``fixed_line_for(classification)``, if given, returns a scenario-
        specific fixed line for this turn's classify-stage result (whatever
        extra fields that scenario's own AnalyzeInteraction subclass adds),
        or None; with a line, nothing is generated and the turn decides
        nothing else (``fixed_by_request``). ``classification`` is None for a
        world event (nothing was classified this turn), so the hook is not
        called then.
        """
        current_situation = coerce_situation(current_situation)
        technical_terms: list[str] = []
        classification = None
        if world_event:
            mode = "narrative"
            current_scene = event_scene
        else:
            classification = self.classify(
                current_situation=current_situation,
                user_action=user_action,
                history=history,
            )
            mode = str(classification.interaction_mode)
            current_scene = str(classification.current_scene)
            extracted_terms = [str(term) for term in classification.technical_terms]
            known_terms = find_known_technical_terms(user_action)
            technical_terms = list(dict.fromkeys(extracted_terms + known_terms))
            if technical_terms and mode != "exit":
                mode = "meta"
        # The ending replaces an exit's line anyway.
        fixed_line = (
            fixed_line_for(classification)
            if fixed_line_for is not None and classification is not None and mode != "exit"
            else None
        )
        if fixed_line is not None:
            return dspy.Prediction(
                interaction_mode=mode,
                current_scene=current_scene,
                selected_mishearing="none",
                situation_update=SituationUpdate(),
                updated_situation=current_situation,
                narrative_actions=[],
                settled_details=[],
                bot_response=fixed_line,
                question_kind="none",
                awaiting_reply=False,
                fixed_by_request=True,
            )
        candidates: list[MishearingCandidate] = []
        if mode == "meta" and technical_terms:
            prior_mentions = count_prior_technical_mentions(history, technical_terms)
            if prior_mentions == 0:
                candidates, unknown_terms = known_candidates_for(
                    technical_terms, history
                )
                if unknown_terms:
                    planning = self.plan(
                        current_situation=current_situation,
                        user_utterance=user_action,
                        technical_terms=unknown_terms,
                        history=history,
                    )
                    candidates.extend(planning.candidates)
                if not candidates:
                    candidates = [uncertain_candidate_for(technical_terms[0])]
            elif prior_mentions == 1:
                candidates = [uncertain_candidate_for(technical_terms[0])]
        respond_inputs = dict(
            current_situation=current_situation,
            user_action=user_action,
            history=history,
            world_event=world_event,
            previous_bot_response=previous_bot_response,
            pooh_preferences=pooh_preferences,
            interaction_mode=mode,
            mishearing_candidates=candidates,
        )
        feed = (
            SentenceFeed(on_response)
            if on_response is not None and mode not in ("meta", "exit")
            else None
        )
        if feed is not None:
            response = stream_response(self.respond, feed.feed, **respond_inputs)
        else:
            response = self.respond(**respond_inputs)
        selected_mishearing = response.selected_mishearing
        # Keep the raw LM response here.  Compile-time metrics must see model
        # failures so BootstrapFewShot can learn from them; the optional
        # runtime guard is applied only by the interactive chat loop.
        bot_response = str(response.bot_response)
        uses_uncertain_candidate = any(
            candidate.narrative_link.startswith("技術語を理解できず")
            and candidate.heard_as in bot_response
            for candidate in candidates
        )
        if (
            mode == "meta"
            and contains_technical_term(bot_response, technical_terms)
            and not uses_uncertain_candidate
        ):
            selected_mishearing = "none"
            bot_response = (
                "ぼくにはよくわからないけれど、"
                "いま、きみといっしょにいるのはわかるよ。"
            )
        if feed is not None:
            if not feed.finish(bot_response):
                print(
                    f"警告: 送信済みの文が応答の冒頭と一致しません: "
                    f"{feed.text[:feed.sent]!r} -> {bot_response!r}",
                    file=sys.stderr,
                )
                # The line was written twice (e.g. DSPy retried with another
                # adapter after streaming the first try); what Pooh said is
                # what was sent, so the turn is read and logged from that.
                bot_response = feed.text[:feed.sent]
        elif on_response is not None and mode != "exit":
            on_response(bot_response)
        interpretation = self.interpret(
            current_situation=current_situation,
            history=history,
            user_action=user_action,
            world_event=world_event,
            bot_response=bot_response,
        )
        # The state delta is read from the final line, as the decisions are,
        # so writing the line does not wait for it.
        situation_update = SituationUpdate.model_validate(interpretation.situation_update)
        updated_situation = apply_situation_update(current_situation, situation_update)
        narrative_actions = [
            str(action) for action in getattr(interpretation, "narrative_actions", []) or []
        ]
        question_kind = str(interpretation.question_kind)
        settled_details = [
            SettledDetail.model_validate(detail) for detail in interpretation.settled_details
        ]
        return dspy.Prediction(
            interaction_mode=mode,
            current_scene=current_scene,
            selected_mishearing=selected_mishearing,
            situation_update=situation_update,
            updated_situation=updated_situation,
            narrative_actions=narrative_actions,
            settled_details=settled_details,
            bot_response=bot_response,
            question_kind=question_kind,
            awaiting_reply=question_kind != "none",
            fixed_by_request=False,
        )


def package_version() -> str:
    try:
        return version("dspy")
    except PackageNotFoundError:
        return "not-installed"


def validate_environment(require_api_key: bool = True) -> None:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Python 3.12が必要です（現在: {platform.python_version()}）。")
    installed = package_version()
    if installed != EXPECTED_DSPY_VERSION:
        raise RuntimeError(f"DSPy {EXPECTED_DSPY_VERSION}が必要です（現在: {installed}）。")
    if require_api_key and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEYが設定されていません。")


def serialize_prediction(value: Any) -> str:
    fields = (
        "interaction_mode",
        "current_scene",
        "selected_mishearing",
        "situation_update",
        "narrative_actions",
        "settled_details",
        "bot_response",
        "question_kind",
    )
    payload = {}
    for field in fields:
        item = getattr(value, field, "")
        payload[field] = item.model_dump() if hasattr(item, "model_dump") else item
    updated = getattr(value, "updated_situation", None)
    if updated is not None:
        payload["updated_situation"] = coerce_situation(updated).model_dump()
    return json.dumps(payload, ensure_ascii=False, default=str)


def clamp_score(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def make_metric(judge: Any, meta_evaluator: Any, judge_lm: Any):
    """Build the semantic metric used inside BootstrapFewShot."""

    def metric(gold: Any, pred: Any, trace: Any = None) -> float:
        del trace
        reference = serialize_prediction(gold)
        candidate = serialize_prediction(pred)
        if str(getattr(gold, "interaction_mode", "")) != str(
            getattr(pred, "interaction_mode", "")
        ):
            return 0.0
        # current_scene is an observation shown to the user, not a control or
        # semantic target.  A plausible scene estimate must not disqualify an
        # otherwise good response during BootstrapFewShot.
        if sorted(getattr(gold, "narrative_actions", [])) != sorted(
            getattr(pred, "narrative_actions", [])
        ):
            return 0.0
        # Which details were settled is a hard gate; the wording of each value
        # (e.g. 青 vs 青色) is left to the judge.
        if settled_topics(gold) != settled_topics(pred):
            return 0.0
        # Asking for new ideas or handing Pooh's own decision back stalls the
        # story; the interpretation stage's structured label is used, not text.
        if str(getattr(pred, "question_kind", "none")) in DISALLOWED_QUESTION_KINDS:
            return 0.0
        previous_bot_response = str(getattr(gold, "previous_bot_response", ""))
        # An exact repeat of the prior turn is unambiguous and cheap to check
        # deterministically; no need to spend a judge call on it.
        if previous_bot_response and str(getattr(pred, "bot_response", "")) == previous_bot_response:
            return 0.0
        with dspy.context(lm=judge_lm):
            if str(getattr(gold, "interaction_mode", "")) == "meta":
                meta_assessment = meta_evaluator(
                    user_action=str(getattr(gold, "user_action", "")),
                    history=str(getattr(gold, "history", "")),
                    reference_response=str(getattr(gold, "bot_response", "")),
                    candidate_mode=str(getattr(pred, "interaction_mode", "")),
                    candidate_response=str(getattr(pred, "bot_response", "")),
                )
                if clamp_score(meta_assessment.policy_compliance) < 4:
                    return 0.0
            assessment = judge(
                current_situation=coerce_situation(getattr(gold, "current_situation")),
                user_action=str(getattr(gold, "user_action", "")),
                history=str(getattr(gold, "history", "")),
                previous_bot_response=previous_bot_response,
                reference_output=reference,
                candidate_output=candidate,
            )
        if clamp_score(assessment.conversational_progress) < 4:
            return 0.0
        if clamp_score(assessment.pooh_agency) < 4:
            return 0.0
        scores = [
            clamp_score(assessment.mode_accuracy),
            clamp_score(assessment.response_fit),
            clamp_score(assessment.narrative_coherence),
            clamp_score(assessment.participant_agency),
            clamp_score(assessment.pooh_agency),
            clamp_score(assessment.state_quality),
            clamp_score(assessment.conversational_progress),
        ]
        return sum(scores) / (5 * len(scores))

    return metric


def cache_hash(train_model: str, judge_model: str, scenario: Scenario | None = None) -> str:
    scenario = scenario or get_scenario(DEFAULT_SCENARIO)
    payload = {
        "program_version": PROGRAM_VERSION,
        "metric_version": METRIC_VERSION,
        "dspy_version": EXPECTED_DSPY_VERSION,
        "train_model": train_model,
        "judge_model": judge_model,
        "scenario": scenario.key,
        "optimizer": [
            BOOTSTRAP_METRIC_THRESHOLD,
            MAX_BOOTSTRAPPED_DEMOS,
            MAX_LABELED_DEMOS,
            MAX_TOTAL_DEMOS_PER_STAGE,
            "merge_stage_demos_v3",
        ],
        "full_turn_examples": [item.toDict() for item in scenario.trainset],
        "mode_examples": [item.toDict() for item in scenario.mode_examples],
        "mishearing_examples": [item.toDict() for item in scenario.mishearing_examples],
        "response_examples": [item.toDict() for item in scenario.response_examples],
        "interpret_examples": [item.toDict() for item in scenario.interpret_examples],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def cache_path(train_model: str, judge_model: str, scenario: Scenario | None = None) -> Path:
    return CACHE_DIR / f"pooh_{cache_hash(train_model, judge_model, scenario)}.json"


def configure_models() -> tuple[Any, Any, str, str]:
    api_key = os.environ["OPENAI_API_KEY"]
    train_model = os.getenv("DSPY_TRAIN_MODEL", os.getenv("DSPY_MODEL", DEFAULT_MODEL))
    judge_model = os.getenv("DSPY_JUDGE_MODEL", DEFAULT_JUDGE_MODEL)
    # A stalled request once left Pooh silent for ten minutes; give up and
    # retry instead, and let the caller report a failed turn.
    request_limits = {"timeout": LM_TIMEOUT_SECONDS, "num_retries": LM_NUM_RETRIES}
    train_lm = dspy.LM(train_model, api_key=api_key, **request_limits)
    judge_lm = dspy.LM(judge_model, api_key=api_key, **request_limits)
    dspy.configure(lm=train_lm)
    return train_lm, judge_lm, train_model, judge_model


def set_module_demos(module: Any, demos: list[Any]) -> None:
    """Attach demos to the Predict instances nested in a DSPy module."""
    for predictor in module.predictors():
        predictor.demos = demos


def set_agent_demos(agent: Any, scenario: Scenario) -> None:
    """Give each teacher Predictor only its task-specific examples."""
    set_module_demos(agent.classify, scenario.mode_examples)
    set_module_demos(agent.plan, scenario.mishearing_examples)
    set_module_demos(agent.respond, scenario.response_examples)
    set_module_demos(agent.interpret, scenario.interpret_examples)


def select_labeled_demos(labeled_demos: list[Any], count: int) -> list[Any]:
    """Pick up to `count` labeled demos evenly spaced across the full list,
    so demo diversity never depends on how the curated set happens to be
    ordered in the source file (e.g. grouped by scene for readability).
    A head/tail split silently starves whichever region of the list shares
    a trait (like current_scene) once the file is sorted by that trait;
    even spacing keeps coverage broad regardless of ordering. The first and
    last items are always included when count >= 2."""
    if count <= 0:
        return []
    total = len(labeled_demos)
    if count >= total:
        return list(labeled_demos)
    if count == 1:
        return [labeled_demos[0]]
    selected: list[Any] = []
    seen_indices: set[int] = set()
    for step in range(count):
        index = round(step * (total - 1) / (count - 1))
        if index not in seen_indices:
            seen_indices.add(index)
            selected.append(labeled_demos[index])
    return selected


# Fields that make two demos "the same example" for each stage.  Phrasing
# variants of one response example share history and bot_response, so the
# response stage keeps one of them; the classify stage keeps each distinct
# input because varied phrasings are exactly what it learns from.
CLASSIFY_DEMO_IDENTITY = ("user_action", "history")
PLAN_DEMO_IDENTITY = ("user_utterance", "history", "technical_terms")
RESPOND_DEMO_IDENTITY = ("history", "world_event", "bot_response")
# A bootstrapped trace carries the model's own output, so it is matched to
# its source example by inputs only.
RESPOND_DEMO_INPUTS = ("user_action", "history", "world_event")


def demo_identity(demo: Any, fields: tuple[str, ...]) -> Any:
    if not fields:
        return id(demo)
    return tuple(str(getattr(demo, name, "")) for name in fields)


def unique_demos(
    demos: list[Any], fields: tuple[str, ...], exclude: set[Any] | None = None,
) -> list[Any]:
    """Drop repeats (and anything in `exclude`) while keeping the first order."""
    seen = set(exclude or ())
    unique = []
    for demo in demos:
        key = demo_identity(demo, fields)
        if key in seen:
            continue
        seen.add(key)
        unique.append(demo)
    return unique


def merge_module_demos(
    module: Any,
    labeled_demos: list[Any],
    identity_fields: tuple[str, ...] = (),
    input_fields: tuple[str, ...] | None = None,
) -> None:
    """Keep accepted traces first and fill the rest up to a fixed total
    demo budget per stage, regardless of how large the curated set grows.
    A labeled demo that repeats a trace or another labeled demo would only
    waste a slot, so each example appears at most once."""
    input_fields = identity_fields if input_fields is None else input_fields
    for predictor in module.predictors():
        bootstrapped = unique_demos(list(predictor.demos), input_fields)
        traced_inputs = {demo_identity(demo, input_fields) for demo in bootstrapped}
        used = {
            demo_identity(demo, identity_fields)
            for demo in labeled_demos
            if demo_identity(demo, input_fields) in traced_inputs
        }
        pool = unique_demos(labeled_demos, identity_fields, exclude=used)
        remaining = max(0, MAX_TOTAL_DEMOS_PER_STAGE - len(bootstrapped))
        predictor.demos = bootstrapped + select_labeled_demos(pool, remaining)


def merge_agent_demos(agent: Any, scenario: Scenario) -> None:
    """Merge accepted traces with examples matching each Predictor schema."""
    merge_module_demos(agent.classify, scenario.mode_examples, CLASSIFY_DEMO_IDENTITY)
    merge_module_demos(agent.plan, scenario.mishearing_examples, PLAN_DEMO_IDENTITY)
    merge_module_demos(
        agent.respond, scenario.response_examples, RESPOND_DEMO_IDENTITY, RESPOND_DEMO_INPUTS,
    )
    merge_module_demos(agent.interpret, scenario.interpret_examples, RESPOND_DEMO_INPUTS)


def bootstrap_order(trainset: list[Any]) -> list[Any]:
    """Order the trainset so bootstrapping draws traces from many scenes.

    BootstrapFewShot tries examples in order and stops after a few accepted
    traces, so the file's scene grouping would otherwise decide that every
    trace comes from the first scene.  Scenes take turns, and phrasing
    variants of an example already queued go to the back.
    """
    first_of_group: list[Any] = []
    repeats: list[Any] = []
    seen: set[Any] = set()
    for item in trainset:
        key = demo_identity(item, RESPOND_DEMO_IDENTITY)
        (repeats if key in seen else first_of_group).append(item)
        seen.add(key)
    by_scene: dict[str, list[Any]] = {}
    for item in first_of_group:
        by_scene.setdefault(str(getattr(item, "current_scene", "none")), []).append(item)
    ordered: list[Any] = []
    queues = list(by_scene.values())
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered + repeats


def compile_program(
    train_lm: Any, judge_lm: Any, train_model: str, judge_model: str, scenario: Scenario
) -> Path:
    judge = dspy.ChainOfThought(NarrativeQualityJudge)
    meta_evaluator = dspy.ChainOfThought(MetaPolicyEvaluator)
    optimizer = BootstrapFewShot(
        metric=make_metric(judge, meta_evaluator, judge_lm),
        metric_threshold=BOOTSTRAP_METRIC_THRESHOLD,
        max_bootstrapped_demos=MAX_BOOTSTRAPPED_DEMOS,
        max_labeled_demos=MAX_LABELED_DEMOS,
    )
    classify_signature, interpret_signature, response_signature = signatures_for(scenario)
    student = PoohNarrativeAgent(
        classify_signature=classify_signature, interpret_signature=interpret_signature,
        response_signature=response_signature,
    )
    teacher = PoohNarrativeAgent(
        classify_signature=classify_signature, interpret_signature=interpret_signature,
        response_signature=response_signature,
    )
    set_agent_demos(teacher, scenario)
    with dspy.context(lm=train_lm):
        program = optimizer.compile(
            student=student,
            teacher=teacher,
            trainset=bootstrap_order(scenario.trainset),
        )
    merge_agent_demos(program, scenario)
    target = cache_path(train_model, judge_model, scenario)
    target.parent.mkdir(parents=True, exist_ok=True)
    program.save(str(target))
    return target


def load_compiled_program(train_model: str, judge_model: str, scenario: Scenario) -> Any:
    target = cache_path(train_model, judge_model, scenario)
    if not target.exists():
        raise FileNotFoundError(
            f"コンパイル済みプログラムがありません: {target}\n"
            "`python scripts/pooh_narrative_dspy.py --mode compile"
            f" --scenario {scenario.key}` を先に実行してください。"
        )
    classify_signature, interpret_signature, response_signature = signatures_for(scenario)
    program = PoohNarrativeAgent(
        classify_signature=classify_signature, interpret_signature=interpret_signature,
        response_signature=response_signature,
    )
    program.load(str(target))
    return program


@dataclass
class Turn:
    user_action: str
    interaction_mode: str
    bot_response: str
    updated_situation: NarrativeSituation
    scene_id: str = "none"
    source: str = "participant"
    world_event: str = ""
    narrative_actions: list[str] | None = None
    settled_details: list[dict[str, str]] | None = None


@dataclass
class SessionOutput:
    """One browser/CLI/ROS-visible output from a narrative session."""

    source: str
    bot_response: str
    interaction_mode: str
    scene_id: str
    updated_situation: NarrativeSituation
    scene_label: str | None = None
    situation_diff: str = "(変化なし)"
    narrative_actions: list[str] = field(default_factory=list)
    fixed_utterance_id: str | None = None
    world_event_id: str | None = None
    latency_ms: float = 0.0
    generation_fallback: bool = False
    turn: Turn | None = None
    # Set when the line was sent to the robot before the turn finished.
    turn_id: str | None = None
    streamed: bool = False
    # For source "observed": what the robot saw the participant do.
    observed_actions: list[str] = field(default_factory=list)
    observed_with_speech: bool = False

    @property
    def motion(self) -> dict[str, str]:
        """The body motion registered with this fixed line, if any."""
        return fixed_utterance_motion(self.fixed_utterance_id) if self.fixed_utterance_id else {}

    @property
    def current_scene(self) -> str:
        """Compatibility name used by the existing terminal renderer."""
        return self.scene_id

    def to_dict(self) -> dict[str, Any]:
        """Return only browser-safe public fields, excluding internal turns."""
        return {
            "source": self.source,
            "bot_response": self.bot_response,
            "interaction_mode": self.interaction_mode,
            "scene_id": self.scene_id,
            "scene_label": self.scene_label,
            "situation": self.updated_situation.model_dump(),
            "situation_diff": self.situation_diff,
            "narrative_actions": list(self.narrative_actions),
            "motion": self.motion,
            "fixed_utterance_id": self.fixed_utterance_id,
            "world_event_id": self.world_event_id,
            "latency_ms": round(self.latency_ms, 1),
            "generation_fallback": self.generation_fallback,
        }


# Called with (session, output) for every output, before the ROS relay.
OutputListener = Callable[["NarrativeSession", SessionOutput], None]


def settled_topics(value: Any) -> list[str]:
    return sorted(
        SettledDetail.model_validate(detail).topic
        for detail in getattr(value, "settled_details", []) or []
    )


def settled_details_of(result: Any) -> list[dict[str, str]]:
    """Plain dicts for logging; validated again by the controller."""
    return [
        SettledDetail.model_validate(detail).model_dump()
        for detail in getattr(result, "settled_details", [])
    ]


def format_history(turns: list[Turn], max_turns: int = 12) -> str:
    # The current state is passed separately as current_situation; repeating
    # it per turn buries this turn's input under a long history.
    chunks = []
    recent = turns[-max_turns:]
    start = len(turns) - len(recent) + 1
    for index, turn in enumerate(recent, start=start):
        input_line = (
            f"参加者の生入力: {turn.user_action}"
            if turn.source == "participant"
            else f"世界イベント: {turn.world_event}"
        )
        chunks.append(
            f"Turn {index}\n{input_line}\n"
            f"応答モード: {turn.interaction_mode}\n"
            f"参考場面ID: {turn.scene_id}\n"
            f"物語アクション: {turn.narrative_actions or []}\n"
            f"プーの応答: {turn.bot_response}"
        )
    return "\n\n".join(chunks)


def append_log(path: Path, turn: Turn, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**metadata, "timestamp": datetime.now(timezone.utc).isoformat(), **asdict(turn)}
    record["updated_situation"] = turn.updated_situation.model_dump()
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_session_record(path: Path, record: dict[str, Any]) -> None:
    """A log line that is not a turn (the session's close)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**record, "timestamp": datetime.now(timezone.utc).isoformat()}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def invoke(
    agent: Any,
    situation: NarrativeSituation,
    action: str,
    history: str,
    *,
    world_event: str = "",
    event_scene: str = "none",
    previous_bot_response: str = "",
    pooh_preferences: str = "",
    on_response: Callable[[str], None] | None = None,
    fixed_line_for: Callable[[Any], str | None] | None = None,
) -> tuple[Any, float]:
    started = time.perf_counter()
    # Only passed when used, so agents without these hooks keep working.
    hooks = {
        name: value
        for name, value in (("on_response", on_response), ("fixed_line_for", fixed_line_for))
        if value is not None
    }
    result = agent(
        current_situation=situation,
        user_action=action,
        history=history,
        world_event=world_event,
        event_scene=event_scene,
        previous_bot_response=previous_bot_response,
        pooh_preferences=pooh_preferences,
        **hooks,
    )
    return result, (time.perf_counter() - started) * 1000


_COLOR_ENABLED = sys.stdout.isatty()
_ANSI_RESET = "\033[0m" if _COLOR_ENABLED else ""
_ANSI_POOH = "\033[33m" if _COLOR_ENABLED else ""  # yellow
_ANSI_PARTICIPANT = "\033[36m" if _COLOR_ENABLED else ""  # cyan


def pooh_line(text: str) -> str:
    return f"{_ANSI_POOH}プー: {text}{_ANSI_RESET}"


def normalize_scene_id(scene_id: str, scenario: Scenario) -> str:
    return scene_id if scenario.scene_label(scene_id) is not None else "none"


_SITUATION_SCALAR_FIELDS = (("place", "場所"), ("purpose", "場面の目的"), ("relationship", "関係"))
_SITUATION_LIST_FIELDS = (
    ("characters", "登場人物"),
    ("props", "小道具と状態"),
    ("events", "重要な出来事"),
    ("unresolved", "未解決・未確定"),
)


def format_situation_diff(
    previous: NarrativeSituation | None,
    current: NarrativeSituation,
) -> str:
    """Render only what changed this turn.

    Debugging a missed narrative_action (e.g. a decision that should have
    been recorded but wasn't) means noticing that a field did NOT change;
    that is easy to miss by eye in two full situation blocks printed one
    after another, but obvious when the diff has nothing to show for it.
    """
    if previous is None:
        return str(current)
    lines: list[str] = []
    for field, label in _SITUATION_SCALAR_FIELDS:
        old_value = getattr(previous, field)
        new_value = getattr(current, field)
        if old_value != new_value:
            lines.append(f"【{label}】{old_value} → {new_value}")
    for field, label in _SITUATION_LIST_FIELDS:
        old_values = list(getattr(previous, field))
        new_values = list(getattr(current, field))
        added = [value for value in new_values if value not in old_values]
        removed = [value for value in old_values if value not in new_values]
        if added:
            lines.append(f"【{label}: 追加】" + "、".join(added))
        if removed:
            lines.append(f"【{label}: 削除】" + "、".join(removed))
    return "\n".join(lines) if lines else "(変化なし)"


def print_result(
    label: str,
    result: Any,
    scenario: Scenario,
    previous_situation: NarrativeSituation | None = None,
) -> None:
    print(f"\n--- {label} ---")
    print(f"[応答モード] {result.interaction_mode}")
    scene_id = normalize_scene_id(str(getattr(result, "current_scene", "none")), scenario)
    scene_label = scenario.scene_label(scene_id)
    print(f"[参考場面] {scene_label or '該当なし'}")
    print(f"[物語アクション] {list(getattr(result, 'narrative_actions', []))}")
    print(f"[場面の変化]\n{format_situation_diff(previous_situation, result.updated_situation)}")
    print(pooh_line(result.bot_response))


def read_console_input(prompt: str, timeout: float | None) -> str | None:
    """Read one line while allowing a pending timed event to wake the loop."""
    print(prompt, end="", flush=True)
    readable, _, _ = select.select([sys.stdin], [], [], timeout)
    if not readable:
        print(_ANSI_RESET)
        return None
    line = sys.stdin.readline()
    if line == "":
        raise EOFError
    print(_ANSI_RESET, end="")
    return line.strip()


def create_event_controller(
    scenario: Scenario,
    clock: Callable[[], float] = time.monotonic,
) -> NarrativeEventController | None:
    if scenario.event_controller_factory is None:
        return None
    return scenario.event_controller_factory(clock)


# The conventional event id a scenario's own required_events uses for "close
# the session after enough silence once the story is wrapped up"; stripped by
# --no-idle-close. A scenario without such an event is unaffected.
IDLE_CLOSE_EVENT_ID = "idle_close_after_wrap_up"


def without_idle_close(
    factory: Callable[[Callable[[], float]], NarrativeEventController] | None,
) -> Callable[[Callable[[], float]], NarrativeEventController] | None:
    """Wrap a scenario's event-controller factory (``--no-idle-close``) so
    the story never auto-closes on silence after its wrap-up; a no-op for a
    scenario with no event controller, or none named ``IDLE_CLOSE_EVENT_ID``."""
    if factory is None:
        return None

    def wrapped(clock: Callable[[], float]) -> NarrativeEventController:
        controller = factory(clock)
        controller.required_events = tuple(
            event for event in controller.required_events
            if event.event_id != IDLE_CLOSE_EVENT_ID
        )
        return controller

    return wrapped


def _event_result(
    agent: Any,
    current_situation: NarrativeSituation,
    event: WorldEvent,
    history: str,
    previous_bot_response: str = "",
    pooh_preferences: str = "",
    follow_up: bool = False,
) -> tuple[Any, float, bool]:
    if event.fixed_response:
        line = event.fallback_response
        if follow_up and event.follow_up_response is not None:
            line = event.follow_up_response
        return dspy.Prediction(
            interaction_mode="narrative",
            current_scene=event.scene_id,
            selected_mishearing="none",
            situation_update=SituationUpdate(),
            updated_situation=current_situation,
            narrative_actions=[],
            settled_details=[],
            bot_response=line,
        ), 0.0, False
    try:
        result, latency_ms = invoke(
            agent,
            current_situation,
            "",
            history,
            world_event=event.description,
            event_scene=event.scene_id,
            previous_bot_response=previous_bot_response,
            pooh_preferences=pooh_preferences,
        )
        return result, latency_ms, False
    except Exception:
        # The event is already committed. Language generation may fall back,
        # but it must not roll the world state back or fire the event twice.
        return dspy.Prediction(
            interaction_mode="narrative",
            current_scene=event.scene_id,
            selected_mishearing="none",
            situation_update=SituationUpdate(),
            updated_situation=current_situation,
            narrative_actions=[],
            settled_details=[],
            bot_response=event.fallback_response,
        ), 0.0, True


class NarrativeSession:
    """Stateful session API shared by the terminal, Web UI, and ROS relay.

    Each instance owns its story state, history, timers, lifecycle guards, and
    log file.  Callers must serialize calls for one instance; different
    instances are independent and may be used for different participants.
    """

    def __init__(
        self,
        agent: Any,
        model_name: str,
        program_id: str,
        scenario: Scenario,
        *,
        event_controller: NarrativeEventController | None = None,
        ros_publisher: NarrativeRelayPublisher | None = None,
        session_id: str | None = None,
        output_listeners: Sequence[OutputListener] = (),
    ) -> None:
        self.agent = agent
        self.model_name = model_name
        self.program_id = program_id
        self.scenario = scenario
        self.current_situation = scenario.initial_situation.model_copy(deep=True)
        self.turns: list[Turn] = []
        self.controller = event_controller or create_event_controller(scenario)
        self.ros_publisher = ros_publisher
        self.output_listeners = tuple(output_listeners)
        self.session_id = session_id or uuid4().hex
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        self.log_path = LOG_DIR / f"session_{timestamp}_{self.session_id[:8]}.jsonl"
        self.started = False
        self.ended = False
        # Whether Pooh's latest answer asked the participant something.
        self.awaiting_reply = False

    def _history(self) -> str:
        return (
            f"会話の冒頭\nプーの応答: {self.scenario.opening_line}\n\n"
            f"{format_history(self.turns)}"
        )

    def _notify_listeners(self, output: SessionOutput) -> None:
        for listener in self.output_listeners:
            try:
                listener(self, output)
            except Exception as exc:
                # A listener (e.g. the picture book) never stops the story.
                print(f"[{self.session_id[:8]}] 出力リスナーのエラー: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)

    def _publish(self, output: SessionOutput) -> None:
        self._notify_listeners(output)
        if self.ros_publisher is None:
            return
        self.ros_publisher.publish(
            bot_response=output.bot_response,
            narrative_actions=output.narrative_actions,
            scene_id=output.scene_id,
            source=output.source,
            world_event_id=output.world_event_id,
            fixed_utterance_id=output.fixed_utterance_id,
            turn_id=output.turn_id,
            streamed=output.streamed,
            interaction_mode=output.interaction_mode,
        )

    def start(self) -> SessionOutput | None:
        """Start once and expose the scripted opening to every output path."""
        if self.started or self.ended:
            return None
        self.started = True
        output = SessionOutput(
            source="session_open",
            bot_response=self.scenario.opening_line,
            interaction_mode="narrative",
            scene_id="none",
            updated_situation=self.current_situation,
            situation_diff=format_situation_diff(None, self.current_situation),
            fixed_utterance_id=fixed_utterance_id(self.scenario.opening_line),
        )
        if self.controller is not None:
            self.controller.begin(self.controller.speech_seconds(output.bot_response))
        self._publish(output)
        return output

    def close(self, reason: str = "requested", ending_line: bool = True) -> SessionOutput | None:
        """Close once with the scenario's fixed ending line, or without one
        (``ending_line=False``, the story started over), sent to no robot."""
        if self.ended:
            return None
        self.ended = True
        line = self.scenario.ending_line if ending_line else ""
        output = SessionOutput(
            source="session_close",
            bot_response=line,
            interaction_mode="exit",
            scene_id="none",
            updated_situation=self.current_situation,
            fixed_utterance_id=fixed_utterance_id(line) if line else None,
        )
        if self.log_path.exists():
            # Only a session that has logged turns gets its ending recorded,
            # so the picture book of a past log ends where the session did.
            # This line is not a turn: its source is "session_close".
            append_session_record(self.log_path, {
                "scenario": self.scenario.key,
                "session_id": self.session_id,
                "source": "session_close",
                "close_reason": reason,
                "interaction_mode": "exit",
                "bot_response": output.bot_response,
                "fixed_utterance_id": output.fixed_utterance_id,
            })
        if ending_line:
            self._publish(output)
        else:
            self._notify_listeners(output)
        return output

    def observe(self, actions: Sequence[str], with_speech: bool = False) -> SessionOutput | None:
        """Record what the robot saw the participant do, without a reply.

        The actions (e.g. 「参加者がはちみつの壺をプーの左手に手渡した」) join
        the story's events, so the next answer can take them up; they are
        logged with source "observed" and retold in the picture book, but
        nothing is sent to the robot.  ``with_speech`` marks actions that
        came with the utterance about to be answered (「どうぞ」 said while
        handing), as opposed to a touch, or speech dropped while Pooh was
        busy, reported on their own; the book retells the former together
        with what was said.
        """
        actions = [action for action in actions if action]
        if self.ended or not actions:
            return None
        self.current_situation = apply_situation_update(
            self.current_situation, SituationUpdate(add_events=actions))
        append_session_record(self.log_path, {
            "scenario": self.scenario.key,
            "session_id": self.session_id,
            "source": "observed",
            "observed_actions": actions,
            "with_speech": with_speech,
            "updated_situation": self.current_situation.model_dump(),
        })
        output = SessionOutput(
            source="observed",
            bot_response="",
            interaction_mode="narrative",
            scene_id="none",
            updated_situation=self.current_situation,
            observed_actions=actions,
            observed_with_speech=with_speech,
        )
        self._notify_listeners(output)
        return output

    def seconds_until_due(self) -> float | None:
        if self.ended or self.controller is None:
            return None
        return self.controller.seconds_until_due()

    def poll(self) -> SessionOutput | None:
        """Return one due world event without waiting, if any."""
        return self._fire_event(follow_up=False)

    def follow_up(self) -> SessionOutput | None:
        """Right after answering the participant, fire an overdue event.

        Events that wait for a quiet moment would otherwise never fire while
        the participant keeps talking; this lets them follow Pooh's answer.
        An answer that asked the participant something is left for them to
        reply to; the event then waits for silence or the next answer.
        """
        if self.awaiting_reply:
            return None
        return self._fire_event(follow_up=True)

    def _fire_event(self, follow_up: bool) -> SessionOutput | None:
        if not self.started or self.ended or self.controller is None:
            return None
        event = self.controller.pop_due_event(follow_up=follow_up)
        if event is None:
            return None
        if event.ends_session:
            return self.close(event.event_id)

        previous_situation = self.current_situation
        self.current_situation = apply_situation_update(
            self.current_situation,
            event.situation_update,
        )
        result, latency_ms, used_fallback = _event_result(
            self.agent,
            self.current_situation,
            event,
            self._history(),
            previous_bot_response=self.turns[-1].bot_response if self.turns else "",
            pooh_preferences=relevant_preferences(
                self.current_situation, self.scenario.pooh_preferences
            ),
            follow_up=follow_up,
        )
        proposed = list(getattr(result, "narrative_actions", []))
        details = settled_details_of(result)
        # Only what the story accepted is recorded (see observe_actions).
        actions = self.controller.observe_actions(proposed)
        self.controller.observe_settled_details(details)
        result.updated_situation = self.controller.synchronize_situation(
            result.updated_situation
        )
        result.current_scene = event.scene_id
        turn = Turn(
            user_action="",
            interaction_mode=result.interaction_mode,
            bot_response=str(result.bot_response),
            updated_situation=result.updated_situation,
            scene_id=event.scene_id,
            source="world_event",
            world_event=event.description,
            narrative_actions=actions,
            settled_details=details,
        )
        self.turns.append(turn)
        self.current_situation = result.updated_situation
        utterance_id = fixed_utterance_id(turn.bot_response) if event.fixed_response else None
        append_log(
            self.log_path,
            turn,
            {
                "model": self.model_name,
                "program_id": self.program_id,
                "scenario": self.scenario.key,
                "session_id": self.session_id,
                "world_event_id": event.event_id,
                "fixed_utterance_id": utterance_id,
                "event_follow_up": follow_up,
                "generation_fallback": used_fallback,
                "latency_ms": round(latency_ms, 1),
                **({"proposed_narrative_actions": proposed} if proposed != actions else {}),
            },
        )
        output = SessionOutput(
            source="world_event",
            bot_response=turn.bot_response,
            interaction_mode=turn.interaction_mode,
            scene_id=turn.scene_id,
            updated_situation=turn.updated_situation,
            scene_label=self.scenario.scene_label(turn.scene_id),
            situation_diff=format_situation_diff(
                previous_situation, turn.updated_situation
            ),
            narrative_actions=actions,
            fixed_utterance_id=utterance_id,
            world_event_id=event.event_id,
            latency_ms=latency_ms,
            generation_fallback=used_fallback,
            turn=turn,
        )
        self.controller.observe_activity(self.controller.speech_seconds(turn.bot_response))
        self._publish(output)
        return output

    def submit(self, user_input: str) -> SessionOutput | None:
        """Process one participant utterance or textual physical action."""
        if not self.started:
            raise RuntimeError("セッションを先に開始してください。")
        if self.ended:
            return None
        user_input = user_input.strip()
        if not user_input:
            return None
        if user_input.casefold() == "exit":
            return self.close("exit_command")

        if self.controller is not None:
            self.controller.observe_activity()
        previous_situation = self.current_situation
        history = self._history()
        turn_id = f"{self.session_id[:8]}-{len(self.turns) + 1}"
        sent_early: list[str] = []

        def send_early(text: str) -> None:
            # The robot starts speaking while the line is still being written
            # and interpret still runs; each part follows the one before.
            if self.ros_publisher.publish_chunk(
                turn_id=turn_id, index=len(sent_early), text=text
            ):
                sent_early.append(text)
            elif sent_early:
                print(f"警告: 応答の続きを送れませんでした: {text!r}", file=sys.stderr)

        early = self.ros_publisher is not None and not (
            self.controller.response_may_be_replaced(user_input, history)
            if self.controller is not None
            else False
        )
        result, latency_ms = invoke(
            self.agent,
            self.current_situation,
            user_input,
            history,
            previous_bot_response=self.turns[-1].bot_response if self.turns else "",
            pooh_preferences=relevant_preferences(
                self.current_situation, self.scenario.pooh_preferences
            ),
            on_response=send_early if early else None,
            fixed_line_for=self.controller.fixed_line_for if self.controller is not None else None,
        )
        streamed = bool(sent_early)
        generated_response = str(result.bot_response)
        if self.controller is not None:
            result.bot_response = self.controller.enforce_response_invariants(
                user_input, history, str(result.bot_response),
                narrative_actions=[
                    str(action) for action in getattr(result, "narrative_actions", []) or []
                ],
            )
        if str(result.interaction_mode) == "exit":
            # DSPy decides that the participant is leaving; the goodbye itself
            # is the scenario's fixed ending line, as when the session closes.
            result.bot_response = self.scenario.ending_line
        # The model's decisions and state delta describe the line it wrote;
        # once the runtime guard replaced that line, they no longer apply.
        response_replaced = (
            result.bot_response != generated_response
            or bool(getattr(result, "fixed_by_request", False))
        )
        if streamed and response_replaced:
            # response_may_be_replaced missed a case; the robot has said the
            # generated line, and the replacement is only shown and logged.
            print(
                f"警告: 送信済みの応答が差し替えられました: "
                f"{generated_response!r} -> {result.bot_response!r}",
                file=sys.stderr,
            )
        if response_replaced:
            result.narrative_actions = []
            result.settled_details = []
            result.updated_situation = self.current_situation
        # A runtime replacement is a statement, not a question to the participant.
        self.awaiting_reply = (
            bool(getattr(result, "awaiting_reply", False)) and not response_replaced
        )
        if self.controller is not None:
            # Quiet time and newly armed events count from when Pooh finishes
            # speaking this answer, not from the input or generation.
            self.controller.observe_activity(
                self.controller.speech_seconds(str(result.bot_response)),
                awaiting_reply=self.awaiting_reply,
            )
        actions = list(getattr(result, "narrative_actions", []))
        if self.controller is not None:
            # The runtime guard (enforce_response_invariants) can replace the
            # line with one that implies an action the model's own (discarded)
            # line might not have reported.
            for extra in self.controller.extra_actions_for(str(result.bot_response)):
                if extra not in actions:
                    actions.append(extra)
        details = settled_details_of(result)
        proposed = list(actions)
        if self.controller is not None:
            # Only what the story accepted is recorded (see observe_actions).
            actions = self.controller.observe_actions(proposed)
            self.controller.observe_settled_details(details)
            result.updated_situation = self.controller.synchronize_situation(
                result.updated_situation
            )
        result.current_scene = normalize_scene_id(
            str(getattr(result, "current_scene", "none")),
            self.scenario,
        )
        warn_if_response_repeated(
            result.bot_response,
            self.turns[-1].bot_response if self.turns else None,
        )
        is_exit = str(result.interaction_mode) == "exit"
        turn = Turn(
            user_action=user_input,
            interaction_mode=str(result.interaction_mode),
            bot_response=str(result.bot_response),
            updated_situation=result.updated_situation,
            scene_id=result.current_scene,
            narrative_actions=actions,
            settled_details=details,
        )
        self.turns.append(turn)
        self.current_situation = result.updated_situation
        # A generated line that happens to be a fixed one can reuse its WAV too.
        utterance_id = fixed_utterance_id(turn.bot_response)
        append_log(
            self.log_path,
            turn,
            {
                "model": self.model_name,
                "program_id": self.program_id,
                "scenario": self.scenario.key,
                "session_id": self.session_id,
                "fixed_utterance_id": utterance_id,
                "latency_ms": round(latency_ms, 1),
                "response_replaced": response_replaced,
                "question_kind": str(getattr(result, "question_kind", "none")),
                **({"proposed_narrative_actions": proposed} if proposed != actions else {}),
            },
        )
        if is_exit:
            self.ended = True
        output = SessionOutput(
            source="participant",
            bot_response=turn.bot_response,
            fixed_utterance_id=utterance_id,
            interaction_mode=turn.interaction_mode,
            scene_id=turn.scene_id,
            updated_situation=turn.updated_situation,
            scene_label=self.scenario.scene_label(turn.scene_id),
            situation_diff=format_situation_diff(
                previous_situation, turn.updated_situation
            ),
            narrative_actions=actions,
            latency_ms=latency_ms,
            turn=turn,
            turn_id=turn_id if streamed else None,
            streamed=streamed,
        )
        self._publish(output)
        return output


def run_chat(
    agent: Any,
    model_name: str,
    program_id: str,
    scenario: Scenario,
    *,
    input_fn: Callable[[str], str | None] | None = None,
    event_controller: NarrativeEventController | None = None,
    ros_publisher: NarrativeRelayPublisher | None = None,
    output_listeners: Sequence[OutputListener] = (),
    stop_fn: Callable[[], str | None] | None = None,
) -> bool:
    """One session; ``stop_fn`` returning a reason ends it: with the ending
    line (the robot left the narrative mode), or without it for "restarted".
    False once the input has closed (end of input, Ctrl+C, or the relay gone)."""
    session = NarrativeSession(
        agent,
        model_name,
        program_id,
        scenario,
        event_controller=event_controller,
        ros_publisher=ros_publisher,
        output_listeners=output_listeners,
    )
    print(f"シナリオ: {scenario.label}")
    print(session.current_situation)
    opening = session.start()
    if opening is not None:
        print(f"\n{pooh_line(opening.bot_response)}")
    print("終了するには exit と入力してください。")
    while not session.ended:
        situation_before_event = session.current_situation
        event_output = session.poll()
        if event_output is not None and event_output.source == "session_close":
            print(pooh_line(event_output.bot_response))
            break
        if event_output is not None:
            print_result("Timed Event", event_output, scenario, situation_before_event)
            continue
        try:
            prompt = f"\n{_ANSI_PARTICIPANT}あなたの発話・行為: "
            if input_fn is None:
                timeout = session.seconds_until_due()
                user_input = read_console_input(prompt, timeout)
            else:
                relayed_input = input_fn(prompt)
                if relayed_input is not None:
                    print(f"{prompt}{relayed_input}{_ANSI_RESET}")
                user_input = relayed_input.strip() if relayed_input is not None else None
        except (EOFError, KeyboardInterrupt):
            print(_ANSI_RESET)
            closing = session.close("input_closed")
            if closing is not None:
                print(pooh_line(closing.bot_response))
            return False
        observed = ros_publisher.take_observed_actions() if ros_publisher else []
        if session.observe(observed, with_speech=bool(user_input)) is not None:
            print(f"[観察された行為] {'、'.join(observed)}")
        stop = stop_fn() if stop_fn is not None else None
        if stop:
            closing = session.close(stop, ending_line=stop != "restarted")
            if closing is not None and closing.bot_response:
                print(pooh_line(closing.bot_response))
            break
        if user_input is None:
            continue
        if not user_input:
            print("発話か行為を入力してください。")
            continue
        situation_before_turn = session.current_situation
        output = session.submit(user_input)
        if output is None:
            continue
        if output.source == "session_close":
            print(pooh_line(output.bot_response))
            break
        print_result("Compiled", output, scenario, situation_before_turn)
        situation_before_event = session.current_situation
        follow_up = session.follow_up()
        if follow_up is not None:
            print_result("Timed Event", follow_up, scenario, situation_before_event)
    return True


RELAY_RETRY_SECONDS = 2.0


def run_chat_on_mode(
    agent: Any,
    model_name: str,
    program_id: str,
    scenario: Scenario,
    ros_publisher: NarrativeRelayPublisher,
    output_listeners: Sequence[OutputListener] = (),
    other_scenarios: dict[str, tuple[Any, str, Scenario]] | None = None,
) -> None:
    """A session each time the robot enters the narrative mode; leaving the
    mode ends the session, as does the participant. The story is of the
    scenario the ROS side names, among ``scenario`` and ``other_scenarios``
    (key -> agent, program id, scenario), or else of ``scenario``.

    The ROS side reports the mode (``interaction_mode``); the bridge enters
    it on hearing one of the scenario's ``start_cues`` outside it. Entering
    it again (the dashboard button) starts a running story over, without the
    ending line. Until
    the relay is up, and after it goes, it is connected to again, so the
    chatbot may be started before the bridge; Ctrl+C ends it.
    """
    started = 0
    print("ナラティブモードを待っています。")
    while True:
        try:
            while ros_publisher.narrative_entries <= started:
                try:
                    ros_publisher.receive_user_input()  # speech outside a story is dropped
                except EOFError:
                    time.sleep(RELAY_RETRY_SECONDS)
                    continue
                ros_publisher.take_observed_actions()
        except KeyboardInterrupt:
            return
        started = ros_publisher.narrative_entries
        story_agent, story_program_id, story = (other_scenarios or {}).get(
            ros_publisher.narrative_scenario, (agent, program_id, scenario))
        ros_publisher.scenario_key = story.key
        if not run_chat(
            story_agent, model_name, story_program_id, story,
            input_fn=ros_publisher.receive_user_input,
            ros_publisher=ros_publisher,
            output_listeners=output_listeners,
            stop_fn=lambda: ("restarted" if ros_publisher.narrative_entries > started
                             else None if ros_publisher.narrative_mode else "mode_changed"),
        ) and ros_publisher.connected:
            return  # Ctrl+C; a closed relay is waited for again
        print("\nナラティブモードを待っています。")


def run_comparison(compiled: Any, scenario: Scenario) -> None:
    action = input("比較する発話・行為: ").strip()
    if not action:
        print("空入力のため比較を終了します。")
        return
    classify_signature, interpret_signature, response_signature = signatures_for(scenario)
    predict = PoohNarrativeAgent(
        dspy.Predict, classify_signature=classify_signature, interpret_signature=interpret_signature,
        response_signature=response_signature,
    )
    set_agent_demos(predict, scenario)
    chain = PoohNarrativeAgent(
        dspy.ChainOfThought,
        classify_signature=classify_signature, interpret_signature=interpret_signature,
        response_signature=response_signature,
    )
    set_agent_demos(chain, scenario)
    agents = {"Predict": predict, "ChainOfThought": chain, "Compiled": compiled}
    for label, agent in agents.items():
        result, _ = invoke(
            agent, scenario.initial_situation, action, "",
            pooh_preferences=relevant_preferences(
                scenario.initial_situation, scenario.pooh_preferences
            ),
        )
        print_result(label, result, scenario)


def build_parser() -> argparse.ArgumentParser:
    """The command-line options (also read by scripts/launch.py, which offers
    only the ones this version has)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("chat", "compile", "compare"), default="chat")
    scenario_options = parser.add_mutually_exclusive_group()
    scenario_options.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default=DEFAULT_SCENARIO,
        help="使用する物語シナリオ。",
    )
    scenario_options.add_argument(
        "--scenarios", nargs="+", choices=[*sorted(SCENARIOS), "all"],
        help="ROSのモード切替で選べるシナリオ。空白区切り、先頭が初期選択。allで全部。",
    )
    parser.add_argument(
        "--ros-relay",
        action="store_true",
        help="指定時、TCPのROS中継から音声入力を受け、各ターンを送り返す。",
    )
    parser.add_argument(
        "--ros-relay-host",
        default="127.0.0.1",
        help="ROS中継のTCPホスト（デフォルト: 127.0.0.1）。",
    )
    parser.add_argument(
        "--ros-relay-port",
        type=int,
        default=8765,
        help="ROS中継のTCPポート（デフォルト: 8765）。",
    )
    parser.add_argument(
        "--extra-scenario",
        action="append",
        default=[],
        choices=[*sorted(SCENARIOS), "all"],
        help="--start-on-mode と使う。ROS側が選べるシナリオを追加する（複数可）。"
             "all で全シナリオ。それぞれコンパイル済みであること。",
    )
    parser.add_argument(
        "--no-idle-close",
        action="store_true",
        help="締めのあと沈黙が続いても物語を終えない。参加者のお別れや、"
             "--start-on-mode ではモードを抜けたときに終わる。",
    )
    parser.add_argument(
        "--start-on-mode",
        action="store_true",
        help="--ros-relay と使う。ロボットがナラティブモードに入るたびに物語を始め、"
             "抜けると終わりのセリフで終える（中継が閉じるまで繰り返す）。",
    )
    add_storybook_arguments(parser)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.scenarios is not None:
        if args.extra_scenario:
            parser.error("--scenarios と --extra-scenario は併用できません。")
        if args.mode != "chat" or not (args.start_on_mode and args.ros_relay):
            parser.error("--scenarios は --mode chat --ros-relay --start-on-mode と使います。")
    return args


def add_storybook_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--storybook-generate", action="store_true",
        help="会話に合わせた挿絵をバックグラウンド生成する（画像APIを使用）。",
    )
    parser.add_argument(
        "--storybook-eink",
        action="store_true",
        help="会話を絵本にして reMarkable（Storybook アプリ, EINK_HOST）にライブで書く。",
    )
    parser.add_argument(
        "--storybook-preview",
        type=Path,
        default=None,
        metavar="DIR",
        help="ライブの絵本のページをPNGとしてDIRに書き出す（タブレットなしの確認用）。",
    )
    parser.add_argument(
        "--postcards", action="store_true",
        help="お話のゴールに届いたら（届かなければ会話が終わったら）、そのセッションのポストカード（印刷用PDF）を裏で作る"
             "（ログディレクトリの postcards/。Web UI ではこの PC の画面からだけ開ける。docs/postcard.md）",
    )
    parser.add_argument(
        "--postcard-size", choices=("hagaki", "l"), default="l",
        help="--postcards の紙: l（L判 89×119mm、SELPHY の L、既定）／hagaki（はがき）",
    )


def create_storybook_listeners(args: argparse.Namespace) -> list[OutputListener]:
    listeners = []
    illustrations = None
    if getattr(args, "storybook_generate", False):
        from storybook_illustrations import SessionIllustrations

        illustrations = SessionIllustrations()
        listeners.append(illustrations)
    if args.storybook_eink or args.storybook_preview:
        from storybook_live import create_live_storybook

        live = create_live_storybook(eink=args.storybook_eink, preview_dir=args.storybook_preview,
                                     illustrations=illustrations)
        if live is not None:
            listeners.append(live)
    if getattr(args, "postcards", False):
        # Every session's postcards, once its pictures are drawn. Last, so
        # that on closing the pictures are finished before the cards.
        from postcard_service import PostcardMaker

        from functools import partial

        from postcard_service import make_postcards

        listeners.append(PostcardMaker(
            illustrations=illustrations,
            make=partial(make_postcards, paper=getattr(args, "postcard_size", "l"))))
    return listeners


def main() -> int:
    args = parse_args()
    try:
        validate_environment()
        requested = args.scenarios or [args.scenario, *args.extra_scenario]
        scenarios = [get_scenario(key) for key in selected_scenario_keys(requested[0], requested[1:])]
        if getattr(args, "no_idle_close", False):
            # Timings are not part of the compiled program, so no recompiling.
            scenarios = [
                replace(s, event_controller_factory=without_idle_close(s.event_controller_factory))
                for s in scenarios
            ]
        scenario = scenarios[0]
        train_lm, judge_lm, train_model, judge_model = configure_models()
        if args.mode == "compile":
            target = compile_program(train_lm, judge_lm, train_model, judge_model, scenario)
            print(f"コンパイル済みプログラムを保存しました: {target}")
            return 0
        compiled = load_compiled_program(train_model, judge_model, scenario)
        if args.mode == "compare":
            run_comparison(compiled, scenario)
        else:
            ros_publisher = (
                NarrativeRelayPublisher(args.ros_relay_host, args.ros_relay_port, scenario.key,
                                        start_cues={s.key: s.start_cues for s in scenarios})
                if args.ros_relay
                else None
            )
            listeners = create_storybook_listeners(args)
            if ros_publisher is not None:
                from postcard_service import PostcardMaker, RobotPostcardStatus

                receipt = RobotPostcardStatus(PROJECT_ROOT / ".tools" / "robot_postcards.json",
                                              enabled=args.postcards)
                for listener in listeners:
                    if isinstance(listener, PostcardMaker):
                        listener.on_status = receipt.update
                listeners.insert(0, receipt)
            try:
                if args.start_on_mode and ros_publisher is not None:
                    run_chat_on_mode(
                        compiled,
                        train_model,
                        cache_hash(train_model, judge_model, scenario),
                        scenario,
                        ros_publisher,
                        output_listeners=listeners,
                        other_scenarios={
                            s.key: (load_compiled_program(train_model, judge_model, s),
                                    cache_hash(train_model, judge_model, s), s)
                            for s in scenarios[1:]
                        },
                    )
                else:
                    run_chat(
                        compiled,
                        train_model,
                        cache_hash(train_model, judge_model, scenario),
                        scenario,
                        input_fn=ros_publisher.receive_user_input if ros_publisher else None,
                        ros_publisher=ros_publisher,
                        output_listeners=listeners,
                    )
            finally:
                # Let the picture book write the ending before exiting.
                for listener in listeners:
                    listener.close()
        return 0
    except Exception as exc:
        # APIキーやLMリクエスト本文を含む可能性のある詳細tracebackは表示しない。
        print(f"エラー: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
