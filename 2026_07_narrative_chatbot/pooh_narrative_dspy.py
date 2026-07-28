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
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

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
from mishearing_examples import MISHEARING_EXAMPLES
from pooh_examples import INITIAL_SITUATION, MODE_EXAMPLES, RESPONSE_EXAMPLES, TRAINSET


PROGRAM_VERSION = "pooh-multistage-v7"
METRIC_VERSION = "mode-aware-judge-v7"
EXPECTED_DSPY_VERSION = "3.2.1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
LOG_DIR = Path(os.getenv("POOH_LOG_DIR", "logs"))
CACHE_DIR = Path(os.getenv("POOH_CACHE_DIR", ".dspy_cache"))
BOOTSTRAP_METRIC_THRESHOLD = 0.8
MAX_BOOTSTRAPPED_DEMOS = 4

class AnalyzeInteraction(dspy.Signature):
    """参加者の発話・行為に最も自然な応答方針を選ぶ。

    ロボット本体、機械、内部機構、技術、研究、実験への言及を含む場合はmetaとする。
    物語世界内の食事や行為と組み合わされた質問でも、技術的な前提を含めばmetaとする。
    直前のmetaへの訂正、言い換え、補足もmetaを維持する。
    終了、拒否、不快、安全に関する意思はexitを優先する。
    """

    current_situation: str = dspy.InputField()
    user_action: str = dspy.InputField()
    history: str = dspy.InputField()
    technical_terms: list[str] = dspy.OutputField(
        desc="発話に含まれる物語世界外の技術語・研究語。存在しない場合は空。"
    )
    interaction_mode: str = dspy.OutputField(
        desc="ordinary、narrative、meta、exitのいずれか。直前のmetaへの訂正・補足もmeta。"
    )


class PlanMishearing(dspy.Signature):
    """抽出済みの技術語について、物語世界内の聞き違い候補を作る。

    既存例の音の近さと物語への接続方法を参考にする。履歴にある聞き違いは繰り返さず、
    自然な候補がなければ空にする。
    """

    current_situation: str = dspy.InputField()
    user_utterance: str = dspy.InputField()
    technical_terms: list[str] = dspy.InputField()
    history: str = dspy.InputField()
    candidates: list[MishearingCandidate] = dspy.OutputField(desc="自然に利用できる0〜3件の候補。")


class GeneratePoohResponse(dspy.Signature):
    """分類結果と候補を参考に、プーとして短く自然に応答する。"""

    current_situation: str = dspy.InputField()
    user_action: str = dspy.InputField()
    history: str = dspy.InputField()
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
    updated_situation: str = dspy.OutputField()
    bot_response: str = dspy.OutputField(
        desc=(
            "参加者に提示する短く自然で穏やかなセリフ。"
            "物語世界外の技術語全体を直接出さない。"
            "不確かな短い音の候補があれば、その音を使って不理解を示す。"
            "理解したような肯定、説明、自己同定をしない。"
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

    current_situation: str = dspy.InputField()
    user_action: str = dspy.InputField()
    history: str = dspy.InputField()
    reference_output: str = dspy.InputField()
    candidate_output: str = dspy.InputField()
    mode_accuracy: int = dspy.OutputField(desc="interaction_modeの分類精度。1〜5。")
    response_fit: int = dspy.OutputField(
        desc="ordinaryを過剰に物語化せず、metaの聞き違い方針とexitを守るなど、モードへの適合度。1〜5。"
    )
    narrative_coherence: int = dspy.OutputField(desc="必要な場合の物語的一貫性。1〜5。")
    participant_agency: int = dspy.OutputField(desc="参加者の行為主体性。1〜5。")
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


class PoohNarrativeAgent(dspy.Module):
    """Compiled multi-stage agent; the planner runs only for meta input."""

    def __init__(self, predictor_factory: Any = dspy.ChainOfThought) -> None:
        super().__init__()
        self.classify = predictor_factory(AnalyzeInteraction)
        self.plan = predictor_factory(PlanMishearing)
        self.respond = predictor_factory(GeneratePoohResponse)

    def forward(self, current_situation: str, user_action: str, history: str) -> Any:
        classification = self.classify(
            current_situation=current_situation,
            user_action=user_action,
            history=history,
        )
        mode = str(classification.interaction_mode)
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
            interaction_mode=mode,
            mishearing_candidates=candidates,
        )
        selected_mishearing = response.selected_mishearing
        bot_response = response.bot_response
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
            selected_mishearing=selected_mishearing,
            updated_situation=response.updated_situation,
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
        "selected_mishearing",
        "updated_situation",
        "bot_response",
    )
    return json.dumps({field: str(getattr(value, field, "")) for field in fields}, ensure_ascii=False)


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
                current_situation=str(getattr(gold, "current_situation", "")),
                user_action=str(getattr(gold, "user_action", "")),
                history=str(getattr(gold, "history", "")),
                reference_output=reference,
                candidate_output=candidate,
            )
        scores = [
            clamp_score(assessment.mode_accuracy),
            clamp_score(assessment.response_fit),
            clamp_score(assessment.narrative_coherence),
            clamp_score(assessment.participant_agency),
            clamp_score(assessment.state_quality),
        ]
        return sum(scores) / (5 * len(scores))

    return metric


def cache_hash(train_model: str, judge_model: str) -> str:
    payload = {
        "program_version": PROGRAM_VERSION,
        "metric_version": METRIC_VERSION,
        "dspy_version": EXPECTED_DSPY_VERSION,
        "train_model": train_model,
        "judge_model": judge_model,
        "optimizer": [BOOTSTRAP_METRIC_THRESHOLD, MAX_BOOTSTRAPPED_DEMOS, len(TRAINSET)],
        "full_turn_examples": [item.toDict() for item in TRAINSET],
        "mode_examples": [item.toDict() for item in MODE_EXAMPLES],
        "mishearing_examples": [item.toDict() for item in MISHEARING_EXAMPLES],
        "response_examples": [item.toDict() for item in RESPONSE_EXAMPLES],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def cache_path(train_model: str, judge_model: str) -> Path:
    return CACHE_DIR / f"pooh_{cache_hash(train_model, judge_model)}.json"


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


def compile_program(train_lm: Any, judge_lm: Any, train_model: str, judge_model: str) -> Path:
    judge = dspy.ChainOfThought(NarrativeQualityJudge)
    meta_evaluator = dspy.ChainOfThought(MetaPolicyEvaluator)
    optimizer = BootstrapFewShot(
        metric=make_metric(judge, meta_evaluator, judge_lm),
        metric_threshold=BOOTSTRAP_METRIC_THRESHOLD,
        max_bootstrapped_demos=MAX_BOOTSTRAPPED_DEMOS,
        max_labeled_demos=len(TRAINSET),
    )
    student = PoohNarrativeAgent()
    set_module_demos(student.classify, MODE_EXAMPLES)
    set_module_demos(student.plan, MISHEARING_EXAMPLES)
    set_module_demos(student.respond, RESPONSE_EXAMPLES)
    with dspy.context(lm=train_lm):
        program = optimizer.compile(student=student, trainset=TRAINSET)
    target = cache_path(train_model, judge_model)
    target.parent.mkdir(parents=True, exist_ok=True)
    program.save(str(target))
    return target


def load_compiled_program(train_model: str, judge_model: str) -> Any:
    target = cache_path(train_model, judge_model)
    if not target.exists():
        raise FileNotFoundError(
            f"コンパイル済みプログラムがありません: {target}\n"
            "`python pooh_narrative_dspy.py --mode compile` を先に実行してください。"
        )
    program = PoohNarrativeAgent()
    program.load(str(target))
    return program


@dataclass
class Turn:
    user_action: str
    interaction_mode: str
    bot_response: str
    updated_situation: str


def format_history(turns: list[Turn], max_turns: int = 6) -> str:
    chunks = []
    recent = turns[-max_turns:]
    start = len(turns) - len(recent) + 1
    for index, turn in enumerate(recent, start=start):
        chunks.append(
            f"Turn {index}\n参加者の生入力: {turn.user_action}\n"
            f"応答モード: {turn.interaction_mode}\n"
            f"プーの応答: {turn.bot_response}\n更新後の状態: {turn.updated_situation}"
        )
    return "\n\n".join(chunks)


def append_log(path: Path, turn: Turn, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**metadata, "timestamp": datetime.now(timezone.utc).isoformat(), **asdict(turn)}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def invoke(agent: Any, situation: str, action: str, history: str) -> tuple[Any, float]:
    started = time.perf_counter()
    result = agent(current_situation=situation, user_action=action, history=history)
    return result, (time.perf_counter() - started) * 1000


def print_result(label: str, result: Any) -> None:
    print(f"\n--- {label} ---")
    print(f"[応答モード] {result.interaction_mode}")
    print(f"[場面の更新] {result.updated_situation}")
    print(f"プー: {result.bot_response}")


def run_chat(agent: Any, model_name: str, program_id: str) -> None:
    current_situation = INITIAL_SITUATION
    turns: list[Turn] = []
    session = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_path = LOG_DIR / f"session_{session}.jsonl"
    print("プーとのお茶会")
    print(current_situation)
    print("\nプー: 今日は来てくれて、ありがとう。今からお茶会をするところなんだ。")
    print("終了するには exit と入力してください。")
    while True:
        try:
            user_input = input("\nあなたの発話・行為: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() == "exit":
            break
        if not user_input:
            print("発話か行為を入力してください。")
            continue
        result, latency_ms = invoke(agent, current_situation, user_input, format_history(turns))
        print_result("Compiled", result)
        turn = Turn(
            user_action=user_input,
            interaction_mode=result.interaction_mode,
            bot_response=result.bot_response,
            updated_situation=result.updated_situation,
        )
        turns.append(turn)
        current_situation = result.updated_situation
        append_log(
            log_path,
            turn,
            {"model": model_name, "program_id": program_id, "latency_ms": round(latency_ms, 1)},
        )
        if result.interaction_mode == "exit":
            return
    print("プー: またね。いっしょに過ごせて、うれしかったよ。")


def run_comparison(compiled: Any) -> None:
    action = input("比較する発話・行為: ").strip()
    if not action:
        print("空入力のため比較を終了します。")
        return
    predict = PoohNarrativeAgent(dspy.Predict)
    set_module_demos(predict.classify, MODE_EXAMPLES)
    set_module_demos(predict.plan, MISHEARING_EXAMPLES)
    set_module_demos(predict.respond, RESPONSE_EXAMPLES)
    chain = PoohNarrativeAgent(dspy.ChainOfThought)
    set_module_demos(chain.classify, MODE_EXAMPLES)
    set_module_demos(chain.plan, MISHEARING_EXAMPLES)
    set_module_demos(chain.respond, RESPONSE_EXAMPLES)
    agents = {"Predict": predict, "ChainOfThought": chain, "Compiled": compiled}
    for label, agent in agents.items():
        result, _ = invoke(agent, INITIAL_SITUATION, action, "")
        print_result(label, result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("chat", "compile", "compare"), default="chat")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_environment()
        train_lm, judge_lm, train_model, judge_model = configure_models()
        if args.mode == "compile":
            target = compile_program(train_lm, judge_lm, train_model, judge_model)
            print(f"コンパイル済みプログラムを保存しました: {target}")
            return 0
        compiled = load_compiled_program(train_model, judge_model)
        if args.mode == "compare":
            run_comparison(compiled)
        else:
            run_chat(compiled, train_model, cache_hash(train_model, judge_model))
        return 0
    except Exception as exc:
        # APIキーやLMリクエスト本文を含む可能性のある詳細tracebackは表示しない。
        print(f"エラー: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
