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


from pooh_examples import INITIAL_SITUATION, TRAINSET


PROGRAM_VERSION = "pooh-interaction-modes-v7"
METRIC_VERSION = "mode-aware-judge-v5"
EXPECTED_DSPY_VERSION = "3.2.1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
LOG_DIR = Path(os.getenv("POOH_LOG_DIR", "logs"))
CACHE_DIR = Path(os.getenv("POOH_CACHE_DIR", ".dspy_cache"))
BOOTSTRAP_METRIC_THRESHOLD = 0.8
MAX_BOOTSTRAPPED_DEMOS = 4

class PoohNarrativeInteraction(dspy.Signature):
    """参加者の発話・行為に応じて、プーとして自然に応答する。

    ordinaryは過剰に物語化せず，普通に答える。
    narrativeは、物語内の出来事として受け止める。
    metaは、初回は音の近い物語世界内の語へ聞き違え、その聞き違えた語をセリフ中に明示してから接続する。
    訂正や反復後は技術語を理解したと示さず、
    同じ聞き違いを繰り返さず、曖昧な関心をプーの感覚、記憶、関心へ移す。
    exit、拒否、不快、安全に関する意思は聞き違えず尊重する。

    参加者が行ったことを無効化せず、プーだけで出来事を完結させない。
    現在の状態や履歴で存在が確定していない人物、小道具、食べ物を、
    すでに存在するものとして断定しない。

    プーは穏やかで、少しのんびり考える。食いしん坊で甘いもの、特に蜂蜜が好き。
    知らないことを知っているふりはせず、身近な出来事について素朴に考える。
    元気すぎる接客口調や、参加者を先導する進行役のような話し方は避ける。
    """

    current_situation: str = dspy.InputField(desc="会話継続に必要な自足的な現在状態。")
    user_action: str = dspy.InputField(desc="解釈を付けていない参加者の生の発話または身体的行為。")
    history: str = dspy.InputField(desc="直近ターンの生入力、モード、応答、更新後状態。")
    interaction_mode: str = dspy.OutputField(
        desc="narrative、ordinary、meta、exitのいずれか。入力に最も自然な応答方針。"
    )
    updated_situation: str = dspy.OutputField(
        desc="次ターンで使う自足的な全状態。実際の変化だけを反映し、挨拶等では維持してよい。"
    )
    bot_response: str = dspy.OutputField(
        desc=(
            "参加者に提示するプーの短く自然で穏やかなセリフ。内部分析を含めない。"
            "参加者が用いた物語世界外の技術語や技術概念を直接出さない。"
        )
    )


class MetaPolicyEvaluator(dspy.Signature):
    """メタ入力への候補応答が、物語世界へ接続する方針を守るか評価する。

    技術語を復唱・説明する応答や、技術概念を理解した上で「ぼくはロボットではない」
    などと自己否定する応答は低く評価する。初回のメタ入力では音の近い物語世界内の
    語への聞き違いを求め、その聞き違えた語が候補応答に明示されていなければ低く評価する。
    履歴に訂正や反復があれば、同じ聞き違いを繰り返さず、発話全体の曖昧な
    関心をプーの感覚、記憶、関心へ移した応答を高く評価する。参加者の技術的関心は
    内部状態に保持してよいが、候補応答には物語世界外の技術語や技術概念を直接出さない。
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
            "メタ応答方針への適合度。初回なのに音の近い聞き違え語を明示しない場合を含め、"
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
    fields = ("interaction_mode", "updated_situation", "bot_response")
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
        "examples": [item.toDict() for item in TRAINSET],
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


def compile_program(train_lm: Any, judge_lm: Any, train_model: str, judge_model: str) -> Path:
    judge = dspy.ChainOfThought(NarrativeQualityJudge)
    meta_evaluator = dspy.ChainOfThought(MetaPolicyEvaluator)
    optimizer = BootstrapFewShot(
        metric=make_metric(judge, meta_evaluator, judge_lm),
        metric_threshold=BOOTSTRAP_METRIC_THRESHOLD,
        max_bootstrapped_demos=MAX_BOOTSTRAPPED_DEMOS,
        max_labeled_demos=len(TRAINSET),
    )
    with dspy.context(lm=train_lm):
        program = optimizer.compile(
            student=dspy.ChainOfThought(PoohNarrativeInteraction),
            trainset=TRAINSET,
        )
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
    program = dspy.ChainOfThought(PoohNarrativeInteraction)
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
    agents = {
        "Predict": dspy.Predict(PoohNarrativeInteraction),
        "ChainOfThought": dspy.ChainOfThought(PoohNarrativeInteraction),
        "Compiled": compiled,
    }
    agents["Predict"].demos = TRAINSET
    for predictor in agents["ChainOfThought"].predictors():
        predictor.demos = TRAINSET
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
