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
import select
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

# DSPyのリクエストキャッシュもホームではなく、このプロジェクト内へ隔離する。
os.environ.setdefault(
    "DSPY_CACHEDIR",
    str(Path(__file__).resolve().parent / ".dspy_cache" / "requests"),
)

try:
    import dspy
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
from narrative_events import HoneyGiftEventController, NarrativeAction, WorldEvent
from pooh_examples import (
    INITIAL_SITUATION,
    MISHEARING_EXAMPLES,
    MODE_EXAMPLES,
    RESPONSE_EXAMPLES,
    TRAINSET,
)
from narrative_relay import NarrativeRelayPublisher
from scenarios import DEFAULT_SCENARIO, SCENARIOS, Scenario, get_scenario


PROGRAM_VERSION = "pooh-structured-state-v21"
METRIC_VERSION = "structured-state-judge-v17"
EXPECTED_DSPY_VERSION = "3.2.1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
LOG_DIR = Path(os.getenv("POOH_LOG_DIR", "logs"))
CACHE_DIR = Path(os.getenv("POOH_CACHE_DIR", ".dspy_cache"))
BOOTSTRAP_METRIC_THRESHOLD = 0.8
MAX_BOOTSTRAPPED_DEMOS = 4
MAX_LABELED_DEMOS = 0
MAX_TOTAL_DEMOS_PER_STAGE = 12

class AnalyzeInteraction(dspy.Signature):
    """参加者の発話・行為に最も自然な応答方針を選ぶ。

    ロボット本体、機械、内部機構、技術、研究、実験への言及を含む場合はmetaとする。
    物語世界内の食事や行為と組み合わされた質問でも、技術的な前提を含めばmetaとする。
    直前のmetaへの訂正、言い換え、補足もmetaを維持する。
    終了、拒否、不快、安全に関する意思はexitを優先する。
    場面の場所・小道具・登場人物・目的、これまでのやり取りで積み重なった出来事、
    または原作（他のクマのプーさんの話）にあるプーの過去のエピソードに関わる発話や
    身体的行為はnarrativeとする。
    挨拶、相槌、間投詞、「これでいい？」「続けてもいい？」のような進行確認など、
    物語世界の出来事を伴わない発話だけをordinaryとする。
    場面についての問いかけへの「迷う」「わからない」も、履歴を踏まえてnarrativeとする。
    判断に迷う場合はordinaryではなくnarrativeを優先する。
    """

    current_situation: NarrativeSituation = dspy.InputField()
    user_action: str = dspy.InputField()
    history: str = dspy.InputField()
    technical_terms: list[str] = dspy.OutputField(
        desc="発話に含まれる物語世界外の技術語・研究語。存在しない場合は空。"
    )
    interaction_mode: str = dspy.OutputField(
        desc="ordinary、narrative、meta、exitのいずれか。直前のmetaへの訂正・補足もmeta。"
    )
    current_scene: str = dspy.OutputField(
        desc=(
            "観察用の場面ID。会話例に定義された場面のうち、現在の展開に最も近い"
            "安定したIDを返す。順序や状態遷移の制御には使わない。該当しなければnone。"
        )
    )


class PlanMishearing(dspy.Signature):
    """抽出済みの技術語について、物語世界内の聞き違い候補を作る。

    既存例の音の近さと物語への接続方法を参考にする。履歴にある聞き違いは繰り返さず、
    自然な候補がなければ空にする。
    """

    current_situation: NarrativeSituation = dspy.InputField()
    user_utterance: str = dspy.InputField()
    technical_terms: list[str] = dspy.InputField()
    history: str = dspy.InputField()
    candidates: list[MishearingCandidate] = dspy.OutputField(desc="自然に利用できる0〜3件の候補。")


class GeneratePoohResponse(dspy.Signature):
    """分類結果と候補を参考に、プーとして短く自然に応答する。

    プー自身の好み・考え・次にしたいことは、プー自身が決める。参加者自身の考えや行動を
    尋ねるのは良い（例:「きみは何をあげる？」）が、
    プー自身が決めるべきことについて参加者に委ねてはいけない（例:「きみはどう思う？」）。
    参加者が尋ねたことを、答えずにそのまま聞き返してもいけない
    （例：「どんなお菓子があるの？」→「どんなお菓子が良いかな」）。
    毎回、応答の最後を問いかけで締めくくる必要はない。自分の考えや感想だけで
    終えてよい。previous_bot_responseと同じ、または意味的に同じ内容を繰り返さない。
    参加者が迷ったり思いつかなかったりしたら、場面に沿った具体案を一つ、
    プー自身の考えとして理由とともに示す。参加者の同意や行動は決めつけない。
    質問にはまず答える。知らない事実は知らないと伝え、プー自身の提案を添える
    （例：相手の好みの色を知らなくても、「青が好きそうだと思うよ」のように
    自分の考えを示す）。「きみは知ってる？」「何か思いついた？」などで
    同じ問いを参加者へ戻さない。
    質問だけでなく、説明や安心させるセリフも履歴から繰り返さない。言い換えだけも避ける。
    「いいね」などの相づちには短く受け止めるだけでもよい。進めるならプー自身の
    小さな次の行動や考えを一つ示し、参加者の行動や場面の結末を勝手に決めない。
    聞き直しや確認には必要な内容を再提示してよい。新しい内容のために、未確認の
    好みや物資の補充を捏造しない。心配にはその内容に即した具体的な工夫で応じる。
    world_event は既に確定した事実として扱う。
    """

    current_situation: NarrativeSituation = dspy.InputField()
    user_action: str = dspy.InputField()
    history: str = dspy.InputField()
    world_event: str = dspy.InputField(
        desc="参加者の入力とは別に、既に確定・適用された世界の出来事。通常ターンは空文字列。"
    )
    previous_bot_response: str = dspy.InputField(
        desc=(
            "直前のプー自身の応答。これと同じ、または意味的に同じ内容("
            "同じ質問・同じ説明・同じ安心させるセリフ等)を繰り返さない。"
            "最初のターンでは空文字列。"
        )
    )
    pooh_preferences: str = dspy.InputField(
        desc=(
            "プー自身が持つ既定の好み(参考情報)。参加者が既に具体的な答えを"
            "示している場合はそれを尊重し、この好みで上書きしない。参加者が"
            "迷ったり答えなかったりした場合にだけ、自分の意見としてここから"
            "提示してよい。空文字列なら特に無い。"
        )
    )
    interaction_mode: str = dspy.InputField()
    mishearing_candidates: list[MishearingCandidate] = dspy.InputField(
        desc=(
            "利用可能な聞き違い候補。候補がある場合はその中から一つを使用し、"
            "候補にない聞き違いを新しく作らない。候補が空なら聞き違いを作らない。"
        )
    )
    selected_mishearing: str = dspy.OutputField(desc=(
            "使用した候補のheard_as。候補が空の場合のみnone。"
        ))
    situation_update: SituationUpdate = dspy.OutputField(
        desc=(
            "このターンで実際に生じた差分だけ。変更しない項目はnullまたは空リスト。"
            "現在状態の保持と差分の適用はPythonが行う。"
        )
    )
    narrative_actions: list[NarrativeAction] = dspy.OutputField(
        desc=(
            "Pythonが検証する機械可読な提案。必要なものだけを返す。利用可能: "
            "commit_honey_jar_gift、cancel_honey_jar_gift、"
            "give_honey_jar_to_participant、deliver_honey_jar_to_eeyore、"
            "block_pooh_honey_access、resolve_empty_jar_gift(空になった壺を"
            "どう贈るか、具体的な内容によらず決着した場合)、"
            "resolve_balloon_color(贈り物にする風船の色が、誰の案によるかに"
            "関わらず決着した場合)、"
            "resolve_ribbon_color(リボンの色が、誰の案によるかに関わらず"
            "決着した場合)。該当しなければ空リスト。"
        )
    )
    bot_response: str = dspy.OutputField(
        desc=(
            "参加者に提示する短く自然で穏やかなセリフ。"
            "物語世界外の技術語全体を直接出さず、技術語を直接説明せず、"
            "利用可能な聞き違い候補があれば、その音を使って不理解を示す。"
            "理解したような肯定、説明、自己同定をしない。"
            "プー自身の好み・考え・次にしたいことを参加者に決めさせない。"
            "参加者の質問を答えずにそのまま聞き返さない。"
            "毎回問いかけで終える必要はない。previous_bot_responseと同じ・"
            "意味的に同じ内容を繰り返さない。"
        )
    )


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


def enforce_response_invariants(
    user_action: str,
    history: str,
    bot_response: str,
    gift_status: str = "undecided",
) -> str:
    """Apply small deterministic guards for requirements LM output may violate."""
    hesitation = any(
        marker in user_action for marker in ("うーん", "迷", "まよう", "どうしよう", "わからない")
    )
    asks_back = any(
        marker in bot_response for marker in ("？", "?", "どう思う", "何かいいアイデア", "何か思いつ")
    )
    # This fallback proposes the honey jar as a still-open idea. Once the gift
    # has moved past that stage (committed, eaten, cancelled, delivered), the
    # same word ("どうしよう" etc.) can appear for unrelated reasons (e.g.
    # sympathizing that the honey is gone), and re-injecting this text would
    # contradict what has already happened in the story.
    if gift_status == "undecided" and hesitation and asks_back:
        return "ぼくは、ハチミツの入った壺を贈るのがいいと思うな。甘いものがあると、イーヨーもきっと喜ぶもの。"

    # Do not let the model introduce a prop the participant has not mentioned.
    if user_action.strip() == "いいね" and "風船" not in history and "風船" in bot_response:
        return "うん、そうしよう。イーヨーが喜んでくれるといいなあ。"

    return bot_response


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


class PoohNarrativeAgent(dspy.Module):
    """Compiled multi-stage agent; the planner runs only for meta input."""

    def __init__(self, predictor_factory: Any = dspy.ChainOfThought) -> None:
        super().__init__()
        self.classify = predictor_factory(AnalyzeInteraction)
        self.plan = predictor_factory(PlanMishearing)
        self.respond = predictor_factory(GeneratePoohResponse)

    def forward(
        self,
        current_situation: NarrativeSituation | dict[str, Any],
        user_action: str,
        history: str,
        world_event: str = "",
        event_scene: str = "none",
        previous_bot_response: str = "",
        pooh_preferences: str = "",
    ) -> Any:
        current_situation = coerce_situation(current_situation)
        technical_terms: list[str] = []
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
        response = self.respond(
            current_situation=current_situation,
            user_action=user_action,
            history=history,
            world_event=world_event,
            previous_bot_response=previous_bot_response,
            pooh_preferences=pooh_preferences,
            interaction_mode=mode,
            mishearing_candidates=candidates,
        )
        situation_update = SituationUpdate.model_validate(response.situation_update)
        updated_situation = apply_situation_update(current_situation, situation_update)
        selected_mishearing = response.selected_mishearing
        # Keep the raw LM response here.  Compile-time metrics must see model
        # failures so BootstrapFewShot can learn from them; the optional
        # runtime guard is applied only by the interactive chat loop.
        bot_response = str(response.bot_response)
        narrative_actions = [str(action) for action in response.narrative_actions]
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
                "いま、きみとお茶会をしているのはわかるよ。"
            )
        return dspy.Prediction(
            interaction_mode=mode,
            current_scene=current_scene,
            selected_mishearing=selected_mishearing,
            situation_update=situation_update,
            updated_situation=updated_situation,
            narrative_actions=narrative_actions,
            bot_response=bot_response,
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
        "bot_response",
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
            "merge_stage_demos_v2",
        ],
        "full_turn_examples": [item.toDict() for item in scenario.trainset],
        "mode_examples": [item.toDict() for item in scenario.mode_examples],
        "mishearing_examples": [item.toDict() for item in scenario.mishearing_examples],
        "response_examples": [item.toDict() for item in scenario.response_examples],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def cache_path(train_model: str, judge_model: str, scenario: Scenario | None = None) -> Path:
    return CACHE_DIR / f"pooh_{cache_hash(train_model, judge_model, scenario)}.json"


def configure_models() -> tuple[Any, Any, str, str]:
    api_key = os.environ["OPENAI_API_KEY"]
    train_model = os.getenv("DSPY_TRAIN_MODEL", os.getenv("DSPY_MODEL", DEFAULT_MODEL))
    judge_model = os.getenv("DSPY_JUDGE_MODEL", train_model)
    train_lm = dspy.LM(train_model, api_key=api_key)
    judge_lm = dspy.LM(judge_model, api_key=api_key)
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


def merge_module_demos(module: Any, labeled_demos: list[Any]) -> None:
    """Keep accepted traces first and fill the rest up to a fixed total
    demo budget per stage, regardless of how large the curated set grows."""
    for predictor in module.predictors():
        bootstrapped = list(predictor.demos)
        remaining = max(0, MAX_TOTAL_DEMOS_PER_STAGE - len(bootstrapped))
        predictor.demos = bootstrapped + select_labeled_demos(labeled_demos, remaining)


def merge_agent_demos(agent: Any, scenario: Scenario) -> None:
    """Merge accepted traces with examples matching each Predictor schema."""
    merge_module_demos(agent.classify, scenario.mode_examples)
    merge_module_demos(agent.plan, scenario.mishearing_examples)
    merge_module_demos(agent.respond, scenario.response_examples)


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
    student = PoohNarrativeAgent()
    teacher = PoohNarrativeAgent()
    set_agent_demos(teacher, scenario)
    with dspy.context(lm=train_lm):
        program = optimizer.compile(
            student=student,
            teacher=teacher,
            trainset=scenario.trainset,
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
            "`python pooh_narrative_dspy.py --mode compile"
            f" --scenario {scenario.key}` を先に実行してください。"
        )
    program = PoohNarrativeAgent()
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


def format_history(turns: list[Turn], max_turns: int = 6) -> str:
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
            f"プーの応答: {turn.bot_response}\n更新後の状態: {turn.updated_situation}"
        )
    return "\n\n".join(chunks)


def append_log(path: Path, turn: Turn, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**metadata, "timestamp": datetime.now(timezone.utc).isoformat(), **asdict(turn)}
    record["updated_situation"] = turn.updated_situation.model_dump()
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
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = agent(
        current_situation=situation,
        user_action=action,
        history=history,
        world_event=world_event,
        event_scene=event_scene,
        previous_bot_response=previous_bot_response,
        pooh_preferences=pooh_preferences,
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
) -> HoneyGiftEventController | None:
    if (
        scenario.event_inactivity_delay_seconds is None
        or scenario.honey_tasting_delay_seconds is None
        or scenario.honey_eating_delay_seconds is None
    ):
        return None
    return HoneyGiftEventController(
        scenario.event_inactivity_delay_seconds,
        scenario.honey_tasting_delay_seconds,
        scenario.honey_eating_delay_seconds,
        clock=clock,
    )


def _event_result(
    agent: Any,
    current_situation: NarrativeSituation,
    event: WorldEvent,
    history: str,
    previous_bot_response: str = "",
    pooh_preferences: str = "",
) -> tuple[Any, float, bool]:
    if event.fixed_response:
        return dspy.Prediction(
            interaction_mode="narrative",
            current_scene=event.scene_id,
            selected_mishearing="none",
            situation_update=SituationUpdate(),
            updated_situation=current_situation,
            narrative_actions=[],
            bot_response=event.fallback_response,
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
            bot_response=event.fallback_response,
        ), 0.0, True


def run_chat(
    agent: Any,
    model_name: str,
    program_id: str,
    scenario: Scenario,
    *,
    input_fn: Callable[[str], str] | None = None,
    event_controller: HoneyGiftEventController | None = None,
    ros_publisher: NarrativeRelayPublisher | None = None,
) -> None:
    current_situation = scenario.initial_situation
    turns: list[Turn] = []
    controller = event_controller or create_event_controller(scenario)
    session = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_path = LOG_DIR / f"session_{session}.jsonl"
    print(f"シナリオ: {scenario.label}")
    print(current_situation)
    print(f"\n{pooh_line(scenario.opening_line)}")
    print("終了するには exit と入力してください。")
    while True:
        event = controller.pop_due_event() if controller is not None else None
        if event is not None:
            situation_before_event = current_situation
            current_situation = apply_situation_update(
                current_situation,
                event.situation_update,
            )
            history = f"会話の冒頭\nプーの応答: {scenario.opening_line}\n\n{format_history(turns)}"
            result, latency_ms, used_fallback = _event_result(
                agent,
                current_situation,
                event,
                history,
                previous_bot_response=turns[-1].bot_response if turns else "",
                pooh_preferences=relevant_preferences(current_situation, scenario.pooh_preferences),
            )
            event_narrative_actions = list(getattr(result, "narrative_actions", []))
            controller.observe_actions(event_narrative_actions)
            result.updated_situation = controller.synchronize_situation(
                result.updated_situation
            )
            result.current_scene = event.scene_id
            print_result("Timed Event", result, scenario, situation_before_event)
            if ros_publisher is not None:
                ros_publisher.publish(
                    bot_response=str(result.bot_response),
                    narrative_actions=event_narrative_actions,
                    scene_id=event.scene_id,
                    source="world_event",
                    world_event_id=event.event_id,
                )
            turn = Turn(
                user_action="",
                interaction_mode=result.interaction_mode,
                bot_response=result.bot_response,
                updated_situation=result.updated_situation,
                scene_id=event.scene_id,
                source="world_event",
                world_event=event.description,
                narrative_actions=event_narrative_actions,
            )
            turns.append(turn)
            current_situation = result.updated_situation
            append_log(
                log_path,
                turn,
                {
                    "model": model_name,
                    "program_id": program_id,
                    "scenario": scenario.key,
                    "world_event_id": event.event_id,
                    "generation_fallback": used_fallback,
                    "latency_ms": round(latency_ms, 1),
                },
            )
            continue
        try:
            prompt = f"\n{_ANSI_PARTICIPANT}あなたの発話・行為: "
            if input_fn is None:
                timeout = controller.seconds_until_due() if controller is not None else None
                user_input = read_console_input(prompt, timeout)
            else:
                user_input = input_fn(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print(_ANSI_RESET)
            break
        if user_input is None:
            continue
        if user_input.lower() == "exit":
            break
        if not user_input:
            print("発話か行為を入力してください。")
            continue
        if controller is not None:
            controller.observe_user_input()
        history = f"会話の冒頭\nプーの応答: {scenario.opening_line}\n\n{format_history(turns)}"
        result, latency_ms = invoke(
            agent,
            current_situation,
            user_input,
            history,
            previous_bot_response=turns[-1].bot_response if turns else "",
            pooh_preferences=relevant_preferences(current_situation, scenario.pooh_preferences),
        )
        result.bot_response = enforce_response_invariants(
            user_action=user_input,
            history=history,
            bot_response=str(result.bot_response),
            gift_status=controller.state.gift_status if controller is not None else "undecided",
        )
        if controller is not None:
            controller.observe_actions(
                list(getattr(result, "narrative_actions", []))
            )
            result.updated_situation = controller.synchronize_situation(
                result.updated_situation
            )
        result.current_scene = normalize_scene_id(
            str(getattr(result, "current_scene", "none")),
            scenario,
        )
        warn_if_response_repeated(
            result.bot_response,
            turns[-1].bot_response if turns else None,
        )
        print_result("Compiled", result, scenario, current_situation)
        if ros_publisher is not None:
            ros_publisher.publish(
                bot_response=str(result.bot_response),
                narrative_actions=list(getattr(result, "narrative_actions", [])),
                scene_id=result.current_scene,
                source="participant",
            )
        turn = Turn(
            user_action=user_input,
            interaction_mode=result.interaction_mode,
            bot_response=result.bot_response,
            updated_situation=result.updated_situation,
            scene_id=result.current_scene,
            narrative_actions=list(getattr(result, "narrative_actions", [])),
        )
        turns.append(turn)
        current_situation = result.updated_situation
        append_log(
            log_path,
            turn,
            {
                "model": model_name,
                "program_id": program_id,
                "scenario": scenario.key,
                "latency_ms": round(latency_ms, 1),
            },
        )
        if result.interaction_mode == "exit":
            return
    print(pooh_line("またね。いっしょに過ごせて、うれしかったよ。"))


def run_comparison(compiled: Any, scenario: Scenario) -> None:
    action = input("比較する発話・行為: ").strip()
    if not action:
        print("空入力のため比較を終了します。")
        return
    predict = PoohNarrativeAgent(dspy.Predict)
    set_agent_demos(predict, scenario)
    chain = PoohNarrativeAgent(dspy.ChainOfThought)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("chat", "compile", "compare"), default="chat")
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default=DEFAULT_SCENARIO,
        help="使用する物語シナリオ。",
    )
    parser.add_argument(
        "--ros-relay",
        action="store_true",
        help="指定時、各ターンをTCPのROS中継へ送信する。",
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_environment()
        scenario = get_scenario(args.scenario)
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
                NarrativeRelayPublisher(args.ros_relay_host, args.ros_relay_port, scenario.key)
                if args.ros_relay
                else None
            )
            run_chat(
                compiled,
                train_model,
                cache_hash(train_model, judge_model, scenario),
                scenario,
                ros_publisher=ros_publisher,
            )
        return 0
    except Exception as exc:
        # APIキーやLMリクエスト本文を含む可能性のある詳細tracebackは表示しない。
        print(f"エラー: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
