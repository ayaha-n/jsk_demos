"""Scenario-agnostic DSPy signatures shared by every scenario.

This module must stay free of any scenario-specific content and must never
import a scenario's own events module (``eeyore_birthday_events``,
``footprints_events``, ...) or ``scenarios.py`` itself — those modules import
*from* here (to subclass ``AnalyzeInteraction``/``InterpretTurn``/
``GeneratePoohResponse`` with their own closed vocabulary or extra
instructions, see e.g. ``eeyore_birthday_events.EeyoreInterpretTurn`` and
``footprints_events.FootprintsInterpretTurn``), so the reverse would be a
circular import. ``pooh_narrative_dspy.py`` imports both this module and each
scenario's events module, and wires a scenario's own subclasses in via
``signatures_for``/``SCENARIO_SIGNATURES``.
"""

from __future__ import annotations

from typing import Any, Literal

import dspy

from mishearing_cases import MishearingCandidate
from narrative_state import NarrativeSituation, SituationUpdate
from narrative_events import SettledDetail


class AnalyzeInteraction(dspy.Signature):
    """参加者の発話・行為に最も自然な応答方針を選ぶ。

    ロボット本体、機械、内部機構、技術、研究、実験への言及を含む場合はmetaとする。
    物語世界内の食事や行為と組み合わされた質問でも、技術的な前提を含めばmetaとする。
    直前のmetaへの訂正、言い換え、補足もmetaを維持する。
    終了、拒否、不快、安全に関する意思はexitを優先する。
    「そろそろ行かなきゃ」「もう帰るね」「またね」のような、立ち去る・別れる意思もexitとする。
    場面の場所・小道具・登場人物・目的、これまでのやり取りで積み重なった出来事、
    または原作（他のクマのプーさんの話）にあるプーの過去のエピソードに関わる発話や
    身体的行為はnarrativeとする。
    挨拶、相槌、間投詞、「これでいい？」「続けてもいい？」のような進行確認など、
    物語世界の出来事を伴わない発話だけをordinaryとする。
    場面についての問いかけへの「迷う」「わからない」も、履歴を踏まえてnarrativeとする。
    判断に迷う場合はordinaryではなくnarrativeを優先する。
    """

    current_situation: NarrativeSituation = dspy.InputField()
    # Keep history before the current input so the newest participant line in
    # the prompt is this turn's input, not the previous turn's.
    history: str = dspy.InputField()
    user_action: str = dspy.InputField()
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

    プー自身の好み・考え・次にしたいことは、プー自身が決める。
    問いかけ: 参加者の質問にはまず答える。知らない事実は知らないと伝え、プー自身の
    考えを添える（例: 好きな色を知らなくても「青が好きそうだと思うよ」）。応答は
    問いかけで終える必要はなく、プー自身の考えや次にしたいことで終えるのを基本とする。
    historyのプーの問いかけに倣わない。問いかけてよいのは、すでに出た案を深めるとき
    （例: 風船が出たら「何色にする？」）、参加者自身の考えや行動を尋ねるとき
    （例:「きみは何をあげる？」）、聞き違いを確かめるときだけ。新しい案を参加者に
    求める問いかけ（例:「ほかには？」）や、プーが決めるべきことを委ねたり、尋ねられた
    ことを答えずに返したりする問いかけ（例:「きみはどう思う？」「きみは知ってる？」）は
    しない。案を広げるなら、プー自身の具体案を一つ理由とともに示す。
    参加者が迷ったり思いつかなかったりしたら、場面に沿った具体案を一つ、
    プー自身の考えとして理由とともに示す。参加者の同意や行動は決めつけない。
    previous_bot_responseや履歴と同じ・意味的に同じ内容（質問、説明、安心させる
    セリフ）を繰り返さない。言い換えだけも避ける。書き出しや締めの言い回しも、
    直前の応答と同じ型を続けて使わない。
    current_situationの【未解決・未確定】は、プーが物語を進めるために気にかけている
    目標である。参加者が出した案にプーも賛成なら、「じゃあ、〇〇にしよう」のように
    決まったことをはっきり言葉にする。話がそれて一段落したら、未解決の項目に戻るよう
    プー自身の案を一つ出す。毎ターン話を戻す必要はない。
    「いいね」などの相づちには短く受け止めるだけでもよい。進めるならプー自身の
    小さな次の行動や考えを一つ示し、参加者の行動や場面の結末を勝手に決めない。
    聞き直しや確認には必要な内容を再提示してよい。新しい内容のために、未確認の
    好みや物資の補充を捏造しない。心配にはその内容に即した具体的な工夫で応じる。
    プーは実際にはできないこと・守れないことを言わない。物語はcurrent_situationの
    【場所】で進み、プーはそこから出かけず、その場でできないこと（例: ケーキを焼く、
    花を探しに行く）を今やろうとしない。「あとで一緒に」といった守れない約束もしない。
    そうした案や頼みは「〜だったらいいなあ」のように楽しく受け止めるか軽くはぐらかし、
    この場にあるものや決めることに話を戻す。
    world_event は既に確定した事実として扱う。
    """

    current_situation: NarrativeSituation = dspy.InputField()
    # Keep history before the current input so the newest participant line in
    # the prompt is this turn's input, not the previous turn's.
    history: str = dspy.InputField()
    user_action: str = dspy.InputField()
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
    bot_response: str = dspy.OutputField(
        desc=(
            "参加者に提示する短く自然で穏やかなセリフ。一人称は常に「ぼく」。"
            "物語世界外の技術語全体を直接出さず、技術語を直接説明せず、"
            "利用可能な聞き違い候補があれば、その音を使って不理解を示す。"
            "理解したような肯定、説明、自己同定をしない。"
            "問いかけの方針は上の説明に従う。"
        )
    )


QuestionKind = Literal[
    "none", "deepen", "participant_own", "clarify", "new_idea", "delegate",
]
# Questions that stall the story instead of moving it; see GeneratePoohResponse.
DISALLOWED_QUESTION_KINDS = {"new_idea", "delegate"}


class InterpretTurn(dspy.Signature):
    """確定したこのターンのやり取りから、物語上の判断と状態の差分を構造化する。セリフは書かない。

    参加者の入力とプーの応答の両方を踏まえ、実際に起きたことだけを返す。
    提案・候補・質問の段階と、決まったことを区別する。迷ったら何も返さない。
    """

    current_situation: NarrativeSituation = dspy.InputField()
    history: str = dspy.InputField()
    user_action: str = dspy.InputField()
    world_event: str = dspy.InputField(
        desc="参加者の入力とは別に、既に確定・適用された世界の出来事。通常ターンは空文字列。"
    )
    bot_response: str = dspy.InputField(desc="このターンのプーの応答。確定済み。")
    situation_update: SituationUpdate = dspy.OutputField(
        desc=(
            "このターンのやり取り(参加者の入力とbot_response)で実際に生じた状態の差分だけ。変更しない項目はnullまたは空リスト。"
            "現在状態の保持と差分の適用はPythonが行う。"
        )
    )
    settled_details: list[SettledDetail] = dspy.OutputField(
        desc=(
            "このターンで一つに決まったこと。贈るもの、色、飲み物、ケーキなど何でもよい。"
            "topicは、current_situationの【未解決・未確定】の項目が決まったならその項目名を"
            "そのまま使い、それ以外は短い名詞(例: 飲み物)にする。valueは決まった内容"
            "(例: 青、風船)で、一つに決まっていなければ含めない。"
            "誰の案かは問わない。候補を挙げただけ、質問しただけなら含めない。"
            "current_situationの【決まったこと】にある内容を同じ値で繰り返さない。"
            "該当しなければ空リスト。"
        )
    )
    question_kind: QuestionKind = dspy.OutputField(
        desc=(
            "bot_responseの最後が参加者への問いかけかどうかと、その種類。"
            "none: 問いかけで終わらない(独り言の問いかけを含む)。"
            "deepen: すでに出た案を深める(例: 何色にする？)。"
            "participant_own: 参加者自身の考えや行動を尋ねる(例: きみは何をあげる？)。"
            "clarify: 聞き違いや聞き直しを確かめる。"
            "new_idea: 新しい案を参加者に求める(例: ほかには？)。"
            "delegate: プーが決めるべきことを委ねる、尋ねられたことを答えずに返す"
            "(例: きみはどう思う？)。"
        )
    )


def _signature_field(signature: type, name: str) -> Any:
    """A declared field's object, whether ``signature`` is a real
    dspy.Signature (fields live in .input_fields/.output_fields, keyed by
    name) or tests' fake dspy stub (dspy.Signature = object; fields are
    just plain class attributes, with no such dict at all)."""
    for fields in (
        getattr(signature, "input_fields", {}),
        getattr(signature, "output_fields", {}),
    ):
        if name in fields:
            return fields[name]
    return getattr(signature, name)
