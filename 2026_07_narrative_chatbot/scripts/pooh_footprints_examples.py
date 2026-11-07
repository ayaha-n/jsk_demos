"""Draft full-turn examples for the footprints ("Hunting the Woozle") scenario.

Adapted for one participant: the companion role Piglet and Christopher
Robin's role in the original both fall to ``参加者``. Like eeyore_birthday,
this scenario uses a timed-event controller
(``footprints_events.FootprintsEventController``) for the beats that must
happen with exact wording (the footprint count escalating, then the nudge
toward realizing the tracks are the pair's own): a first attempt at teaching
the compiled model to reproduce these from a small trainset alone did not
generalize reliably, so a Python-owned clock fires them instead. An organic
equivalent (the participant or Pooh covering the same beat before its timer
is due) is reported through the same closed ``narrative_actions`` vocabulary
eeyore_birthday uses (here ``footprints_events.FootprintsAction``), so the
later timed fire is pre-empted instead of duplicating it; only naming, where
the actual chosen text matters, uses the open ``settled_details`` mechanism
(``NAMING_TOPIC``).
"""

import dspy

from footprints_events import (
    APPEARANCE_UNRESOLVED,
    ASK_APPEARANCE_RESPONSE,
    ASK_NAME_RESPONSE,
    ASK_WHAT_NOTICED_RESPONSE,
    FOURTH_ANIMAL_EVENT,
    FOURTH_ANIMAL_RESPONSE,
    IDENTITY_UNRESOLVED,
    NAMING_EVENT,
    NAMING_TOPIC,
    REVEAL_HINT_RESPONSE,
    SECOND_ANIMAL_EVENT,
    START_FOLLOWING_EVENT,
)
from narrative_events import fixed_utterance, SettledDetail
from narrative_state import NarrativeSituation, SituationUpdate, relevant_preferences
from pooh_examples import (
    build_mode_examples,
    build_interpret_examples,
    build_response_examples,
    example,
    example_variants,
)


_TURN1_BOT_RESPONSE = fixed_utterance("footprints.opening")

# _TURN1_BOT_RESPONSE is spoken as the fixed opening line (see
# FOOTPRINTS_OPENING_LINE below) before the participant says anything, so the
# scene's initial state already reflects what it conveys.
FOOTPRINTS_SITUATION = NarrativeSituation(
    place="雪が降ったあとの100エーカーの森、小さな茂みのまわり",
    purpose="プーと参加者が、見つけた足跡の正体を一緒に突き止める",
    characters=["プー", "参加者"],
    props=["雪の積もった地面", "茂み", "足あと（２つ）"],
    events=["プーが雪の上に見慣れない足跡を見つけた"],
    relationship="参加者はプーと一緒に足跡の正体を探る仲間",
    # NAMING_TOPIC is NOT listed here: it only becomes unresolved once Pooh
    # actually asks (footprints_events._fire_ask_name adds it). Listing it
    # from the start leaked pooh_preferences' "「ウズウズ」って呼びたいな。"
    # into turn 1 — before the question was ever posed, relevant_preferences()
    # has no gate on whether a topic has actually been raised yet — so the
    # model volunteered the name immediately instead of waiting to be asked.
    unresolved=[IDENTITY_UNRESOLVED],
)

_TURN1_DISCOVERY_RESPONSE = (
    "まさにそれを自分にきいているんだ。ほら，あの茂みの周りを見て。足跡があるんだ。"
)
_TURN2_NAMING_RESPONSE = fixed_utterance("footprints.naming")

# 追いかけ始めたあとの既知の出来事（場面5以降は時限イベントが足す）。
_FOLLOWING_STARTED_EVENTS = [
    *FOOTPRINTS_SITUATION.events,
    "参加者が足跡に気づいた",
    NAMING_EVENT,
    "ウズウズの見た目について話した",
    START_FOLLOWING_EVENT,
]

FOOTPRINTS_TRAINSET = [
    # --- 場面1：足跡発見（必須。尋ねられた場合／自発的な場合の両方を用意） -----
    # 名前を尋ねる投げかけは、ここでは生成せず、footprints_events.ask_name
    # （固定セリフ、この応答のすぐ後にfollow_upで発火）に任せている。発見と
    # 投げかけが1ターンに圧縮されがちで、別ターンとして安定して生成できなかった。
    *example_variants(
        current_situation=FOOTPRINTS_SITUATION,
        user_action=["何を追ってるの？", "何かって，なに？"],
        history=f"会話の冒頭\nプーの応答: {_TURN1_BOT_RESPONSE}",
        interaction_mode="narrative",
        current_scene="1",
        bot_response=_TURN1_DISCOVERY_RESPONSE,
    ),
    example(
        current_situation=FOOTPRINTS_SITUATION,
        user_action="こんにちは",
        history=f"会話の冒頭\nプーの応答: {_TURN1_BOT_RESPONSE}",
        interaction_mode="narrative",
        current_scene="1",
        bot_response=(
            "ねえ，聞いてよ。ぼくはいま，あの茂みのまわりの足あとの正体を考えているんだ。"
        ),
    ),
    # 正体が何の生き物かという推測（呼び名の提案ではない）：まだ名付けの
    # 話題にすらなっていないこの段階で、そのまま名前として採用してしまうと、
    # 正体当て（IDENTITY_UNRESOLVED）という別の未解決項目への推測を、呼び名の
    # 決定とすり替えてしまう（実機で確認済みの不具合：footprints_events.
    # FootprintsAnalyzeInteraction.proposed_nameが「ゆきうさぎとかなのかな」を
    # 名付けの提案と誤判定し、fixed_line_forがその場で名付けを確定させていた）。
    example(
        current_situation=FOOTPRINTS_SITUATION,
        user_action="ゆきうさぎとかなのかな",
        proposed_name="",
        history=(
            "Turn 1\n参加者の生入力: なにかって？\n応答モード: narrative\n参考場面ID: 1\n"
            "物語アクション: []\nプーの応答: あのね、雪の上に足跡があって、"
            "それが誰のものなのか知りたくて追っているんだ。"
        ),
        interaction_mode="narrative",
        current_scene="1",
        bot_response="ゆきうさぎかもしれないね。どのくらいの大きさなんだろう。",
    ),
    # プーが既に知っている実在の友だちの名前を挙げた場合も、呼び名の提案では
    # なく正体当てへの推測として扱う（上と同じ理由）。「そう呼ぼう」と新しい
    # ニックネームのように受け入れるのは不自然：プーはティガーを個人として
    # 知っているので、本人かどうかを自分の知識に照らして考える反応になる。
    example(
        current_situation=FOOTPRINTS_SITUATION,
        user_action="ティガーとか？",
        proposed_name="",
        history=(
            "Turn 1\n参加者の生入力: なにかって？\n応答モード: narrative\n参考場面ID: 1\n"
            "物語アクション: []\nプーの応答: あのね、雪の上に足跡があって、"
            "それが誰のものなのか知りたくて追っているんだ。"
        ),
        interaction_mode="narrative",
        current_scene="1",
        bot_response=(
            "うーん、ティガーの足あとだったらもっと跳びはねた形があるはずだから、"
            "ちがう気がするな。"
        ),
    ),
    # 参加者が自分で名前を提案する：その名前がそのまま採用される。
    # footprints.ask_name（固定セリフ）への返答として続く。ask_nameが既に
    # 発火した後なので、NAMING_TOPICはここで初めてunresolvedに入る
    # （footprints_events._fire_ask_nameのsituation_update参照）。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "events": [*FOOTPRINTS_SITUATION.events, "参加者が足跡に気づいた"],
                "unresolved": [IDENTITY_UNRESOLVED, NAMING_TOPIC],
            },
            deep=True,
        ),
        user_action="モコモコって呼んだらどうかな",
        proposed_name="モコモコ",
        history=(
            "Turn 2\n世界イベント: プーが参加者に、名前を考えていると伝えた。"
            "これは既に起きた出来事。\n応答モード: narrative\n参考場面ID: 1\n"
            f"物語アクション: []\nプーの応答: {ASK_NAME_RESPONSE}"
        ),
        interaction_mode="narrative",
        current_scene="2",
        settled_details=[SettledDetail(topic=NAMING_TOPIC, value="モコモコ")],
        situation_update=SituationUpdate(
            add_events=["プーと参加者が足跡の主を「モコモコ」と名付けた"],
            remove_unresolved=[NAMING_TOPIC],
            add_unresolved=[APPEARANCE_UNRESOLVED],
        ),
        bot_response="モコモコか、いいね！そうしよう。",
    ),
    # 参加者が迷ったら、プー自身の案を出す（pooh_preferences、聞き返さない）。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "events": [*FOOTPRINTS_SITUATION.events, "参加者が足跡に気づいた"],
                "unresolved": [IDENTITY_UNRESOLVED, NAMING_TOPIC],
            },
            deep=True,
        ),
        user_action="うーん、わからないな",
        history=(
            "Turn 2\n世界イベント: プーが参加者に、名前を考えていると伝えた。"
            "これは既に起きた出来事。\n応答モード: narrative\n参考場面ID: 1\n"
            f"物語アクション: []\nプーの応答: {ASK_NAME_RESPONSE}"
        ),
        interaction_mode="narrative",
        current_scene="2",
        settled_details=[SettledDetail(topic=NAMING_TOPIC, value="ウズウズ")],
        situation_update=SituationUpdate(
            add_events=[NAMING_EVENT],
            remove_unresolved=[NAMING_TOPIC],
            add_unresolved=[APPEARANCE_UNRESOLVED],
        ),
        bot_response=_TURN2_NAMING_RESPONSE,
    ),
    # 名付けの直後、footprints.ask_appearance（固定セリフ）がまだ発火して
    # いない段階で、参加者が自分から見た目について尋ねる場合。settled_details
    # （topic=APPEARANCE_UNRESOLVED）をここで返しておかないと、Pythonの状態機械は
    # 見た目がもう話題になったことを知らず、この直後にask_appearanceが同じ
    # 質問を重ねて尋ねてしまう（実機で確認済みの不具合。
    # footprints_events.FootprintsEventController.observe_settled_details /
    # ask_appearanceのprerequisite参照）。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "events": [
                    *FOOTPRINTS_SITUATION.events,
                    "参加者が足跡に気づいた",
                    NAMING_EVENT,
                ],
                "unresolved": [IDENTITY_UNRESOLVED, APPEARANCE_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="ウズウズはどんな生き物なのかな",
        history=(
            "Turn 3\n応答モード: narrative\n参考場面ID: 2\n"
            f"物語アクション: []\nプーの応答: {_TURN2_NAMING_RESPONSE}"
        ),
        interaction_mode="narrative",
        current_scene="3",
        settled_details=[
            SettledDetail(topic=APPEARANCE_UNRESOLVED, value="ふわふわの茶色い毛で、まん丸い")
        ],
        situation_update=SituationUpdate(remove_unresolved=[APPEARANCE_UNRESOLVED]),
        bot_response="うーん、ぼくも気になっているんだ。ふわふわの茶色い毛で、まん丸いんじゃないかな。",
    ),
    # --- 場面3：見た目を参加者に相談する（内容が参加者の発言で決まる） -----------
    # 見た目を尋ねる投げかけも、footprints_events.ask_appearance（固定セリフ、
    # 名付けの直後にfollow_upで発火）に任せている。回答の扱いだけがここの役目。
    # 参加者が自分の描写を答える：内容がそのまま採用される。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "events": [
                    *FOOTPRINTS_SITUATION.events,
                    "参加者が足跡に気づいた",
                    NAMING_EVENT,
                ],
                "unresolved": [IDENTITY_UNRESOLVED, APPEARANCE_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="ふわふわの毛で、耳が大きいんじゃないかな",
        history=(
            "Turn 4\n世界イベント: プーが参加者に、見た目について尋ねた。"
            "これは既に起きた出来事。\n応答モード: narrative\n参考場面ID: 3\n"
            f"物語アクション: []\nプーの応答: {ASK_APPEARANCE_RESPONSE}"
        ),
        interaction_mode="narrative",
        current_scene="3",
        settled_details=[SettledDetail(topic=APPEARANCE_UNRESOLVED, value="ふわふわの毛で、耳が大きい")],
        situation_update=SituationUpdate(remove_unresolved=[APPEARANCE_UNRESOLVED]),
        bot_response="へえ、おもしろいね！そんな生き物が本当にいたら、ちょっと会ってみたいなあ。",
    ),
    # 参加者が迷ったら、プー自身の案を出す（pooh_preferences、聞き返さない）。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "events": [
                    *FOOTPRINTS_SITUATION.events,
                    "参加者が足跡に気づいた",
                    NAMING_EVENT,
                ],
                "unresolved": [IDENTITY_UNRESOLVED, APPEARANCE_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="うーん、わからないな",
        history=(
            "Turn 4\n世界イベント: プーが参加者に、見た目について尋ねた。"
            "これは既に起きた出来事。\n応答モード: narrative\n参考場面ID: 3\n"
            f"物語アクション: []\nプーの応答: {ASK_APPEARANCE_RESPONSE}"
        ),
        interaction_mode="narrative",
        current_scene="3",
        settled_details=[
            SettledDetail(topic=APPEARANCE_UNRESOLVED, value="ふわふわの茶色い毛で、まん丸い")
        ],
        situation_update=SituationUpdate(remove_unresolved=[APPEARANCE_UNRESOLVED]),
        bot_response="ぼくは、ふわふわの茶色い毛で、まん丸い生き物だと思うな。",
    ),
    # --- 場面4：足跡を追いかけ始める（必須）。narrative_actionsで時限イベントの
    # クロックを起動する（FootprintsEventController.observe_actions）。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "events": _FOLLOWING_STARTED_EVENTS[:-1],
                "unresolved": [IDENTITY_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="追いかけてみようよ",
        history=(
            "Turn 5\n参加者の生入力: ふわふわの毛で、耳が大きいんじゃないかな\n"
            "応答モード: narrative\n参考場面ID: 3\n物語アクション: []\n"
            "プーの応答: へえ、おもしろいね！そんな生き物が本当にいたら、ちょっと会ってみたいなあ。"
        ),
        interaction_mode="narrative",
        current_scene="4",
        narrative_actions=["started_following"],
        situation_update=SituationUpdate(
            add_events=[START_FOLLOWING_EVENT],
        ),
        bot_response="よし，追いかけてみよう。",
    ),
    # 参加者が時限イベントより先に２匹分になったことに気づく：narrative_actions
    # で後続の時限second_animalが重複発火しないようにする。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "events": _FOLLOWING_STARTED_EVENTS,
                "unresolved": [IDENTITY_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="あ！あしあとがふえたよ",
        history=(
            "Turn 5\n参加者の生入力: 追いかけてみようよ\n応答モード: narrative\n"
            "参考場面ID: 4\n物語アクション: ['started_following']\n"
            "プーの応答: よし，追いかけてみよう。"
        ),
        interaction_mode="narrative",
        current_scene="5",
        narrative_actions=["noticed_second_animal"],
        situation_update=SituationUpdate(
            remove_props=["足あと（２つ）"],
            add_props=["足あと（４つ）"],
            add_events=[SECOND_ANIMAL_EVENT],
        ),
        bot_response="ほんとうだ！足あとが４つに増えてる。２匹いるのかもね。はぐれないようにしよう。",
        question_kind="none",
    ),
    # fourth_animal（時限イベント、場面6）のあと、参加者が「見ていないのに
    # なぜ増えたのか」と不審がる場合：もっともらしい、現実的な範囲の説明
    # （気づかないうちに別の足あとが重なった、等）に留め、関係のない要素
    # （「影」など）を持ち出したり、正体そのものを示唆したりしない
    # （実機で確認済みの不具合：「まさか、ぼくたちの影がウズウズになってる」
    # のような、物語に根拠のない、わかりにくい説明をしてしまっていた）。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "props": ["雪の積もった地面", "茂み", "足あと（８つ）"],
                "events": [*_FOLLOWING_STARTED_EVENTS, SECOND_ANIMAL_EVENT, FOURTH_ANIMAL_EVENT],
                "unresolved": [IDENTITY_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="なんでふえてるの？みてないのに",
        history=(
            "Turn 6\n世界イベント: 足跡がさらに増え、４匹分になっていることに"
            "気づいた。これは既に起きた出来事。\n応答モード: narrative\n"
            f"参考場面ID: 6\n物語アクション: []\nプーの応答: {FOURTH_ANIMAL_RESPONSE}"
        ),
        interaction_mode="narrative",
        current_scene="6",
        narrative_actions=[],
        bot_response=(
            "たしかに、見ていないのに増えてるのは不思議だね。ぼくたちが気づかない"
            "うちに、こっそりやってきたのかもしれないよ。"
        ),
    ),
    # 気づきの促し（reveal_prompt）への、正体を言い当ててはいない返答：
    # 「わからない」のようなはっきりした表明も、「え、なんだろう」「なんのこと？」
    # のような曖昧な聞き返しも同列に扱う。どちらも参加者がまだ気づいていない
    # ことに変わりはなく、もう一度あいまいに促すだけでは次のターンも同じやり取りが
    # 続くだけなので、reveal_hintの固定ヒントをそのまま出す（実機フィードバックで
    # 確認：footprints_events.FootprintsAnalyzeInteraction.gave_up_on_noticing /
    # FootprintsEventController.fixed_line_for が判定・差し替えを行う。ここでは
    # その判定のための分類ステージの教師データとして、この返答が
    # gave_up_on_noticing=Trueになることを示す）。
    # 一方、realized_own_footprintsを誤って報告すると、reveal_direct（最後の
    # 直接的な推測）まで丸ごと飛んでしまうので、narrative_actionsはここでは
    # 空のまま（正体を言い当ててはいないため）。
    *example_variants(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "props": ["雪の積もった地面", "茂み", "足あと（８つ）"],
                "events": [
                    *_FOLLOWING_STARTED_EVENTS,
                    "足跡が２匹分になっていることに気づいた",
                    "足跡が４匹分に増えていることに気づいた",
                ],
                "unresolved": [IDENTITY_UNRESOLVED],
            },
            deep=True,
        ),
        user_action=["え、なんだろう", "なんのこと？", "わからないよ", "全然わからないなあ"],
        history=(
            "Turn 7\n世界イベント: プーが参加者に、何か気づかないかと問いかけた。"
            "これは既に起きた出来事。\n応答モード: narrative\n参考場面ID: 7a\n"
            "物語アクション: []\nプーの応答: ねえ，きみ，なにか気づかない？"
        ),
        interaction_mode="narrative",
        current_scene="7a",
        narrative_actions=[],
        gave_up_on_noticing=True,
        bot_response=REVEAL_HINT_RESPONSE,
    ),
    # 気づきの促しに「気づいた！」とだけ答えた（何に気づいたかは言っていない）
    # 場合：まだ言い当てていないので、プーが答えを言ってはいけない。
    # 「なにに気づいたの？」と聞き返す（実ログで確認：
    # logs/session_20261106T210454... では、この返答が「自分たちの足跡だと
    # 言った」と判定され、プーが「その足あとは、ぼくたちのだったんだね」と
    # 答えを先に言ってしまった）。vague_noticing=True、
    # participant_suggested_own_footprints=False の教師データ。聞き返しは
    # FootprintsEventController.fixed_line_for の固定セリフ。
    *example_variants(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "props": ["雪の積もった地面", "茂み", "足あと（８つ）"],
                "events": [
                    *_FOLLOWING_STARTED_EVENTS,
                    "足跡が２匹分になっていることに気づいた",
                    "足跡が４匹分に増えていることに気づいた",
                ],
                "unresolved": [IDENTITY_UNRESOLVED],
            },
            deep=True,
        ),
        user_action=["きづいた！", "わかった！", "あっ、そういうことか"],
        history=(
            "Turn 7\n世界イベント: プーが参加者に、何か気づかないかと問いかけた。"
            "これは既に起きた出来事。\n応答モード: narrative\n参考場面ID: 7a\n"
            "物語アクション: []\nプーの応答: ねえ，きみ，なにか気づかない？"
        ),
        interaction_mode="narrative",
        current_scene="7a",
        narrative_actions=[],
        vague_noticing=True,
        bot_response=ASK_WHAT_NOTICED_RESPONSE,
        question_kind="participant_own",
    ),
    # --- 場面7-best：参加者が時限イベントより先に自分で気づく。
    # narrative_actionsでrealized_own_footprintsを報告し、以降の時限イベント
    # （気づきの誘導3段階）が発火しないようにする。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "props": ["雪の積もった地面", "茂み", "足あと（８つ）"],
                "events": [
                    *_FOLLOWING_STARTED_EVENTS,
                    "足跡が２匹分になっていることに気づいた",
                    "足跡が４匹分に増えていることに気づいた",
                ],
                "unresolved": [IDENTITY_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="あれ、これって、もしかして僕たちの足跡じゃない？",
        history=(
            "Turn 7\n世界イベント: 足跡がさらに増え、４匹分になっていることに気づいた。"
            "これは既に起きた出来事。\n応答モード: narrative\n参考場面ID: 6\n"
            "物語アクション: []\n"
            "プーの応答: まいったなあ。足あとがまた増えて８つになってるよ。"
            "今度は４匹になったのかな。"
        ),
        interaction_mode="narrative",
        current_scene="8",
        narrative_actions=["realized_own_footprints"],
        participant_suggested_own_footprints=True,
        situation_update=SituationUpdate(
            remove_unresolved=[IDENTITY_UNRESOLVED],
            add_events=["実はウズウズの足跡は自分たちの足跡だったとわかった"],
        ),
        bot_response="ああ，わかった，ボクはバカだから，だまされたんだね。ほんとに頭の悪いクマだ。",
    ),
    # reveal_hint（固定セリフ、具体的な手がかりを出す）のあと、参加者が
    # それに納得して「自分たちの足あとかも」と応じる場合：はっきりと
    # 「自分たちの足あとだった」と言い切る。「そっくりさんの足あとを作って
    # しまった」のような、曖昧でわかりにくい結論は避ける（実機で確認済みの
    # 不具合）。
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "props": ["雪の積もった地面", "茂み", "足あと（８つ）"],
                "events": [
                    *_FOLLOWING_STARTED_EVENTS,
                    "足跡が２匹分になっていることに気づいた",
                    "足跡が４匹分に増えていることに気づいた",
                ],
                "unresolved": [IDENTITY_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="わあほんとうだ。わたしたちのそっくりさんなのかな",
        history=(
            "Turn 7\n世界イベント: プーが参加者に、何か気づかないかと問いかけた。"
            "これは既に起きた出来事。\n応答モード: narrative\n参考場面ID: 7a\n"
            f"物語アクション: ['gave_hint_directly']\nプーの応答: {REVEAL_HINT_RESPONSE}"
        ),
        interaction_mode="narrative",
        current_scene="8",
        narrative_actions=["realized_own_footprints"],
        participant_suggested_own_footprints=True,
        situation_update=SituationUpdate(
            remove_unresolved=[IDENTITY_UNRESOLVED],
            add_events=["実はウズウズの足跡は自分たちの足跡だったとわかった"],
        ),
        bot_response="わかった！これはぼくたち自身の足あとだったんだね。さがしまわっている間に足あとを増やしちゃったんだ。おもしろい冒険だったなあ。",
    ),
    # --- 場面8：着地（正体判明・会話続行） -------------------------------------
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "props": ["雪の積もった地面", "茂み", "足あと（８つ）"],
                "purpose": "プーと参加者は、足跡の正体が自分たちだったと気づいた",
                "events": [
                    *_FOLLOWING_STARTED_EVENTS,
                    "足跡が２匹分になっていることに気づいた",
                    "足跡が４匹分に増えていることに気づいた",
                    "実はウズウズの足跡は自分たちの足跡だったとわかった",
                ],
                "unresolved": [],
            },
            deep=True,
        ),
        user_action="君は世界一のクマだよ。",
        history=(
            "Turn 8\n参加者の生入力: あれ、これって、もしかして僕たちの足跡じゃない？\n"
            "応答モード: narrative\n参考場面ID: 8\n物語アクション: []\n"
            "プーの応答: ああ，わかった，ボクはバカだから，だまされたんだね。"
            "ほんとに頭の悪いクマだ。"
        ),
        interaction_mode="narrative",
        current_scene="8",
        bot_response="そう？とにかく，もうおやつのじかんだね。これで解決だね。ほかにも、なにか話したいことある？",
        question_kind="participant_own",
    ),
]


FOOTPRINTS_OPENING_LINE = _TURN1_BOT_RESPONSE


# プー自身が決めるべきことについて、参加者が迷ったときに持つ既定の考え
# (narrative_state.relevant_preferences参照)。
FOOTPRINTS_POOH_PREFERENCES = {
    NAMING_TOPIC: "あしあとが茂みのまわりをぐるぐる回っていて、うずうずしてる感じがするから、「ウズウズ」って呼びたいな。",
    APPEARANCE_UNRESOLVED: "ふわふわの茶色い毛で、まん丸い生き物だと思う。",
}


FOOTPRINTS_MODE_EXAMPLES = build_mode_examples(
    FOOTPRINTS_TRAINSET,
    extra_fields=lambda item: {
        "gave_up_on_noticing": getattr(item, "gave_up_on_noticing", False),
        "vague_noticing": getattr(item, "vague_noticing", False),
        "proposed_name": getattr(item, "proposed_name", ""),
        "participant_suggested_own_footprints": getattr(
            item, "participant_suggested_own_footprints", False
        ),
    },
)
# interpret()だけに見せる例：プーの応答が正体を勝手に言ってしまった場合、
# 参加者は何も言い当てていないのでrealized_own_footprintsではなく、
# pooh_suggested_own_footprintsを報告する（実ログで確認：
# logs/session_20261106T184627896130_3ac3ec64.jsonl では、このプーのセリフを
# 根拠にrealized_own_footprintsが報告され、気づきの誘導が丸ごと止まった）。
# 応答段の手本にはしないよう、FOOTPRINTS_TRAINSETには入れない。
_POOH_LEAKED_TWIST_EXAMPLES = [
    example(
        current_situation=FOOTPRINTS_SITUATION.model_copy(
            update={
                "props": ["雪の積もった地面", "茂み", "足あと（８つ）"],
                "events": [
                    *_FOLLOWING_STARTED_EVENTS,
                    "足跡が２匹分になっていることに気づいた",
                    "足跡が４匹分に増えていることに気づいた",
                ],
                "unresolved": [IDENTITY_UNRESOLVED],
            },
            deep=True,
        ),
        user_action="そうだねえ、",
        history=(
            "Turn 7\n参加者の生入力: まあたふえたの？\n応答モード: narrative\n"
            "参考場面ID: 6\n物語アクション: []\n"
            "プーの応答: そうなんだ。また足あとが増えちゃったね。"
            "これからどうしようか、少し考えないとね。"
        ),
        interaction_mode="narrative",
        current_scene="6",
        narrative_actions=["pooh_suggested_own_footprints"],
        bot_response=(
            "足跡が僕たちのだったなんて、なんだかおかしいね！"
            "でも、これで一緒に新しい冒険に出かけられるね。"
        ),
    ),
]
FOOTPRINTS_INTERPRET_EXAMPLES = build_interpret_examples(
    FOOTPRINTS_TRAINSET + _POOH_LEAKED_TWIST_EXAMPLES
)
FOOTPRINTS_RESPONSE_EXAMPLES = build_response_examples(
    FOOTPRINTS_TRAINSET, preferences=FOOTPRINTS_POOH_PREFERENCES,
)
