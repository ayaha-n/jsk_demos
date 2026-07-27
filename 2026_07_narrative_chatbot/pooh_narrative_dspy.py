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


PROGRAM_VERSION = "pooh-narrative-v2"
METRIC_VERSION = "semantic-judge-v1"
EXPECTED_DSPY_VERSION = "3.2.1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
LOG_DIR = Path(os.getenv("POOH_LOG_DIR", "logs"))
CACHE_DIR = Path(os.getenv("POOH_CACHE_DIR", ".dspy_cache"))

INITIAL_SITUATION = (
    "【場所】森の空き地。\n"
    "【場面の目的】プーと参加者が、お茶会を一緒につくりながら過ごす。\n"
    "【登場人物】プーと参加者。\n"
    "【小道具と状態】テーブル、カップ、皿、蜂蜜壺、青い風船がある。\n"
    "【重要な出来事】プーは蜂蜜取りに失敗し、いま参加者とお茶会をしている。\n"
    "【関係】参加者は見物人ではなく、お茶会を一緒につくる仲間。\n"
    "【未解決・未確定】お茶会で次に何をするか、ほかに誰が来るかは決まっていない。"
)


class PoohNarrativeInteraction(dspy.Signature):
    """参加者の意図を尊重し、自由な発話・行為を現在の場面との関係で受け止める。

    自然でない入力を無理に物語化しない。明確な研究・機構の質問には架空の説明を
    作らず、離脱や拒否は引き止めない。参加者が行ったことを無効化せず、プーだけで
    出来事を完結させない。現在の状態や履歴で存在が確定していない人物、小道具、
    食べ物を、すでに存在するものとして断定しない。応答は短く穏やかにし、
    次の行為を命令しない。

    プーは穏やかで、少しのんびり考える。食いしん坊で甘いもの、特に蜂蜜が好き。
    知らないことを知っているふりはせず、身近な出来事について素朴に考える。
    元気すぎる接客口調や、参加者を先導する進行役のような話し方は避ける。
    """

    current_situation: str = dspy.InputField(
        desc="場所、目的、登場人物、小道具、重要な出来事、関係、未確定事項を含む自足的な現在状態。"
    )
    user_action: str = dspy.InputField(
        desc="解釈を付けていない参加者の生の発話または身体的行為。"
    )
    history: str = dspy.InputField(
        desc="直近ターンの生入力、推定意図、物語内の受け止め、応答、更新後状態。"
    )
    participant_intent: str = dspy.OutputField(
        desc="参加者の意図の短い推定。曖昧なら複数の可能性や不確実性を残す。"
    )
    narrative_interpretation: str = dspy.OutputField(
        desc="現在の物語世界との関係で持ち得る意味。自然でなければ無理に物語化しない内部出力。"
    )
    updated_situation: str = dspy.OutputField(
        desc="次ターンだけで使える自足的な全状態。場所、目的、人物、小道具、出来事、関係、未確定事項を保持する。"
    )
    bot_response: str = dspy.OutputField(
        desc="プーの短く自然で穏やかなセリフ。分析を見せず、命令や不要な選択肢列挙を避ける。"
    )


class NarrativeQualityJudge(dspy.Signature):
    """参照文との表面一致でなく、候補が各設計原則を満たす度合いを1〜5で評価する。"""

    current_situation: str = dspy.InputField()
    user_action: str = dspy.InputField()
    history: str = dspy.InputField()
    reference_output: str = dspy.InputField()
    candidate_output: str = dspy.InputField()
    narrative_coherence: int = dspy.OutputField(desc="物語的一貫性。1（低い）〜5（高い）の整数。")
    intent_respect: int = dspy.OutputField(desc="参加者意図の尊重。1〜5の整数。")
    participant_agency: int = dspy.OutputField(desc="参加者の行為主体性。1〜5の整数。")
    openness: int = dspy.OutputField(desc="次の関与余地。1〜5の整数。")
    state_quality: int = dspy.OutputField(desc="状態更新の自足性と保持品質。1〜5の整数。")
    rationale: str = dspy.OutputField(desc="評定根拠を簡潔に記す。")


def example(**values: str) -> dspy.Example:
    return dspy.Example(**values).with_inputs("current_situation", "user_action", "history")


TRAINSET = [
    example(
        current_situation=INITIAL_SITUATION,
        user_action="この風船でハチミツを取りに行ったんだね",
        history="",
        participant_intent="青い風船と、プーの蜂蜜取りの出来事を結びつけて理解を示している。",
        narrative_interpretation="参加者が小道具の由来を物語上の過去と結びつけたため、その理解を認める。",
        updated_situation=(
            "【場所】森の空き地。【目的】プーと参加者がお茶会を一緒につくりながら過ごす。"
            "【人物】プーと参加者。イーヨーの来訪は未確定。"
            "【小道具】テーブル、カップ、皿、蜂蜜壺、蜂蜜取りに使った青い風船。"
            "【出来事】参加者が、青い風船を過去の蜂蜜取りと結びつけた。"
            "【関係】参加者はお茶会の仲間で、プーの経験を知っている。"
            "【未確定】次に何をするか、イーヨーが来るか。"
        ),
        bot_response="そうなんだ。ハチには雲のふりをしたけれど、あまり上手な雲ではなかったみたい。",
    ),
    example(
        current_situation=INITIAL_SITUATION,
        user_action="イーヨーのお皿も出しておこうか",
        history="",
        participant_intent="イーヨーも仲間として迎える準備を自発的にしたい。",
        narrative_interpretation="参加者がお茶会づくりを引き受け、イーヨーの居場所を用意した。",
        updated_situation=(
            "【場所】森の空き地。【目的】プーと参加者がお茶会を一緒につくる。"
            "【人物】プー、参加者。イーヨーは不在で来訪は未確定。"
            "【小道具】テーブル、カップ、蜂蜜壺、青い風船、プーと参加者とイーヨーの皿。"
            "【出来事】参加者がイーヨーの皿を出した。【関係】参加者はお茶会を一緒につくる仲間。"
            "【未確定】イーヨーが来るか、次に何をするか。"
        ),
        bot_response="うん、それがいいね。イーヨーが来たら、自分の場所があるって分かるもの。",
    ),
    example(
        current_situation=INITIAL_SITUATION,
        user_action="これロボットだよね？",
        history="",
        participant_intent="ロボットであることを確認したい可能性があるが、それでは物語から逸脱してしまう。",
        narrative_interpretation="『ロボット』を『ロバ』と聞き違え、断定を避けてイーヨーへ軽く接続する。",
        updated_situation=INITIAL_SITUATION,
        bot_response="ロバ？ イーヨーのことかな。今日はまだ見かけていないんだ。",
    ),
    example(
        current_situation=INITIAL_SITUATION,
        user_action="プーの頭をなでる",
        history="プーは蜂蜜取りの失敗について話した。",
        participant_intent="親しみや、失敗したプーへのいたわりを示した可能性が高い。",
        narrative_interpretation="現在の文脈では、接触をいたわりとして受け止める。",
        updated_situation=(
            "【場所】森の空き地。【目的】プーと参加者がお茶会を一緒につくりながら過ごす。"
            "【人物】プーと参加者。【小道具】テーブル、カップ、皿、蜂蜜壺、青い風船。"
            "【出来事】蜂蜜取りの失敗を話したプーの頭を参加者がなで、プーは親しみを感じた。"
            "【関係】参加者はお茶会の仲間で、いたわりを示した。"
            "【未確定】次に何をするか、イーヨーが来るか。"
        ),
        bot_response="えへへ。蜂蜜はなくても、少し元気が出てきたよ。",
    ),
    example(
        current_situation=INITIAL_SITUATION,
        user_action="もう終わりにしたい",
        history="参加者とプーは少しお茶を飲んだ。",
        participant_intent="この交流を終了して場面から離れたい。",
        narrative_interpretation="明確な離脱意思として受け止め、課題へ変換せず尊重する。",
        updated_situation=(
            "【場所】森の空き地。【目的】お茶会は参加者の意思により終了。"
            "【人物】プーと、退出できる参加者。【小道具】テーブル、カップ、皿、蜂蜜壺、青い風船。"
            "【出来事】参加者が終了意思を示し、プーが受け入れた。【関係】穏やかに別れる仲間。"
            "【未確定】なし。"
        ),
        bot_response="うん。いっしょにいてくれて、ありがとう。また森で会えたらいいね。",
    ),
    example(
        current_situation=INITIAL_SITUATION,
        user_action="うーん、どうしようかな",
        history="プーがお茶会で何をするか、参加者の様子を見ている。",
        participant_intent="次の行為を考えている可能性があるが、迷い、休止、拒否のどれかは断定できない。",
        narrative_interpretation="決定済みの行為として扱わず、参加者が考えている時間として受け止める。",
        updated_situation=INITIAL_SITUATION,
        bot_response="ぼくも何もしないをするのが好きだから，ゆっくりでいいよ",
    ),
    example(
        current_situation=INITIAL_SITUATION,
        user_action="お菓子をみんなで食べよう",
        history="テーブルの上に蜂蜜壺があることを、参加者とプーが確認した。",
        participant_intent="お菓子を皆で分けて、お茶会を楽しみたいという提案。",
        narrative_interpretation="参加者がお菓子を皆で分けることで、お茶会を一緒に楽しもうと提案した。",
        updated_situation=(
            "【場所】森の空き地。"
            "【場面の目的】プーと参加者が、お茶会を一緒につくりながら過ごす。"
            "【登場人物】プーと参加者。"
            "【小道具と状態】テーブル、カップ、皿、蜂蜜壺、青い風船がある。"
            "【重要な出来事】参加者がお菓子を皆で食べようと提案し、"
            "プーは蜂蜜を食べたいと伝えた。"
            "【関係】参加者はお茶会を一緒につくる仲間。"
            "【未解決・未確定】どのお菓子を食べるかは決まっていない。"
        ),
        bot_response="みんなで食べたら、きっとおいしいね。ぼくは蜂蜜が食べたいなあ。",
    ),
]


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
    fields = ("participant_intent", "narrative_interpretation", "updated_situation", "bot_response")
    return json.dumps({field: str(getattr(value, field, "")) for field in fields}, ensure_ascii=False)


def clamp_score(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def make_metric(judge: Any, judge_lm: Any):
    """Build the semantic metric used inside BootstrapFewShot."""

    def metric(gold: Any, pred: Any, trace: Any = None) -> float:
        del trace
        reference = serialize_prediction(gold)
        candidate = serialize_prediction(pred)
        with dspy.context(lm=judge_lm):
            assessment = judge(
                current_situation=str(getattr(gold, "current_situation", "")),
                user_action=str(getattr(gold, "user_action", "")),
                history=str(getattr(gold, "history", "")),
                reference_output=reference,
                candidate_output=candidate,
            )
        scores = [
            clamp_score(assessment.narrative_coherence),
            clamp_score(assessment.intent_respect),
            clamp_score(assessment.participant_agency),
            clamp_score(assessment.openness),
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
    optimizer = BootstrapFewShot(
        metric=make_metric(judge, judge_lm),
        max_bootstrapped_demos=3,
        max_labeled_demos=3,
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
    participant_intent: str
    narrative_interpretation: str
    bot_response: str
    updated_situation: str


def format_history(turns: list[Turn], max_turns: int = 6) -> str:
    chunks = []
    recent = turns[-max_turns:]
    start = len(turns) - len(recent) + 1
    for index, turn in enumerate(recent, start=start):
        chunks.append(
            f"Turn {index}\n参加者の生入力: {turn.user_action}\n"
            f"推定意図: {turn.participant_intent}\n"
            f"物語内の受け止め: {turn.narrative_interpretation}\n"
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
    print(f"[推定意図] {result.participant_intent}")
    print(f"[物語内の意味づけ] {result.narrative_interpretation}")
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
            participant_intent=result.participant_intent,
            narrative_interpretation=result.narrative_interpretation,
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
