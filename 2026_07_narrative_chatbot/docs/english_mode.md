# 英語モード（`--language en`）

英語を話す参加者と、日本語のままのプログラムで会話する。

```bash
python scripts/web_server.py --scenario eeyore_birthday --language en \
    --tts-server-url http://127.0.0.1:8100          # 英語の声（pooh_en）のサーバー
python scripts/pooh_narrative_dspy.py --scenario eeyore_birthday --language en
```

ロボットでは、`pooh_bringup.launch` と `pooh_operator.launch` の両方に `language_mode:=en` を付ける（pooh_body の README）。

## しくみ

- **コンパイル済みプログラムは日本語のまま**：参加者の英語を日本語に訳してプログラムに渡し、プーの日本語の返事を英語に訳して出す（`scripts/english_mode.py` の `Translator`、設定済みの LM）。英語用にコンパイルし直す必要はない。
- **セッションの中身は日本語**：履歴、物語の状態、シナリオの出来事の判定、ログの `user_action`・`bot_response` は日本語。英語モードのログには、`language: "en"`、`user_text`（参加者の英語）、`spoken_response`（プーの英語）が加わる。日本語のセッションのログは変わらない。
- **決まったセリフ**：始めと終わりのセリフなど、`config/english.json` の `lines` にあるものは、書いてある英語をそのまま言う。ないものは LM で訳す（一度訳したものは使い回す）。ロボットには決まったセリフの ID を送らない（録音が日本語のため）。
- **少しずつ送らない**：日本語ではプーの返事を文ごとにロボットへ先に送るが、英語では訳し終えてからまとめて送る。

## 聞き間違い

聞き間違いは音に頼るので、英語でだけ別に扱う。

- 技術の言葉は、訳す前の英語で探す（`ENGLISH_MISHEARING_CASES`：robot→Rabbit、camera→Kanga、battery→buttery など）。先に訳すと robot→ロボット→ロバ と日本語の駄洒落になり、英語に戻すと崩れる。
- 表にない言葉は、英語用の予測器（`PlanEnglishMishearing`）が英語の原文から候補を作る。
- 英語用の表・例・予測器は、コンパイル済みプログラムにも `cache_hash()` にも入れない。日本語のコンパイル済みファイルは名前も中身も変わらない。
- 技術の言葉は日本語に訳すときもローマ字のまま残し、プーが聞き間違えた言葉（ローマ字）は英語に訳すときもそのまま残す。

## 画面・絵本・ポストカード・声

| | 英語にするもの | どこ |
|---|---|---|
| Web UI | ボタン・案内・タイトル・場面の説明 | `web/i18n.js`（日本語の文言がキー）、`config/english.json` の `scenarios` |
| 絵本 | タイトル・地の文・「The End」・表紙の「Turn →」・セリフ（“…” you said.） | `config/english.json` の `storybook`、`storybook.english_entries` |
| ポストカード | 英語のセリフを、単語の切れ目で改行して載せる | `postcard.py`（`is_japanese` で分ける） |
| 声 | `pooh_en`（通常のモデル、JP-Extra ではない）。`/api/g2p` は使わず、`language: EN`、速さ 1.0 | `web_tts.VOICES`、pooh_body の `style_bert_vits2_tts.py` |

英語の声のサーバーは日本語用とは別に動かす（1つのサーバーは1つのモデルしか持たない）。既定のポートは 8100。

## 英語にしないもの

- 絵本で、ロボットが見た参加者の動き（「あなたは プーの ひだりてを なでました」）と、プーの身ぶりの説明は省く（日本語の文の組み立てでしか作れないため）。会話はすべて入る。
- シナリオの始まりの合図（`start_cues`）は日本語のまま。ロボットではダッシュボードの **Narrative** ボタンで始める。
- 挿絵を描くための場面の説明は、日本語のまま LM に渡す。
