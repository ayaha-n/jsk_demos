# System Architecture

この文書は、プーとの物語対話システムについて、DSPyコンパイル時とチャット実行時の構成を示す。図中では、教師例や状態などのデータ、Pythonによる規則処理、LLMを使用するDSPy Predictor、生成・保存される結果を色と形で区別している。

## DSPyコンパイル時

![DSPyコンパイル時のシステム構成](<フローチャート - narrative_chatbot_compile.jpg>)

コンパイルは、次の3段階で行われる。

1. `TRAINSET`と固定の聞き間違い候補から、各Predictorに対応する教師例を準備する。
2. 教師例を設定した仮エージェントで一連の処理結果を生成し、モード、メタ方針、返答・状態更新の品質を評価する。
3. 合格したbootstrap例と段階別の手動例を各Predictorへ統合し、Compiled Agentとして保存する。

### 教師例の役割

| データ | 作り方 | 使用先 |
|---|---|---|
| `TRAINSET` | 人が確認した対話全体の正解例 | Agent全体のコンパイル評価 |
| `MODE_EXAMPLES` | `TRAINSET`から分類項目を抽出 | `AnalyzeInteraction` |
| `MISHEARING_EXAMPLES` | `mishearing_cases.py`の代表例から構成 | `PlanMishearing` |
| `RESPONSE_EXAMPLES` | `TRAINSET`から返答・状態差分を抽出 | `GeneratePoohResponse` |

モードが正解と一致しない結果はPythonのhard gateで不採用になる。`meta`の場合はさらに`MetaPolicyEvaluator`が、技術語を直接発話せず物語世界へ接続できているかを評価する。合格後、`NarrativeQualityJudge`が応答方針、物語の一貫性、参加者の主体性、状態更新などを評価する。

評価器はコンパイル時だけ使用され、通常のチャット実行時には呼び出されない。

## チャット実行時

![チャット実行時のシステム構成](<フローチャート - narrative_chatbot_runtime.jpg>)

実行時の処理順序は次のとおりである。

1. `run_chat()`が参加者の入力、現在の物語状態、直近の履歴をCompiled Agentへ渡す。
2. `AnalyzeInteraction`が技術語を抽出し、`ordinary / narrative / meta / exit`の応答モードを判断する。
3. Python処理が登録済み技術語を検索し、必要に応じてモードを`meta`へ補正する。
4. 初出の登録済み語には固定候補を使う。初出の未登録語では`PlanMishearing`が聞き間違い候補を生成する。自然な候補がない場合や訂正時には、Python処理が不確かな短い音または不理解表現を用意する。
5. `GeneratePoohResponse`がプーの返答と、このターンで生じた`SituationUpdate`を生成する。
6. Python処理が技術語の漏出を最終確認し、`SituationUpdate`を現在状態のコピーへ適用する。
7. 完成した`NarrativeSituation`を次ターンへ渡し、会話履歴とJSON Linesログへ保存する。

### 物語状態

物語状態は`narrative_state.py`のPydanticモデルで管理する。

| モデル | 主な内容 |
|---|---|
| `NarrativeSituation` | 場所、目的、登場人物、小道具、出来事、関係、未解決事項 |
| `SituationUpdate` | 場所・目的・関係の変更、人物・小道具・出来事・未解決事項の追加と削除 |

LLMは状態全体を再生成せず、そのターンで変化した差分だけを出力する。`apply_situation_update()`が現在状態をコピーして差分を適用するため、指定されなかった項目は維持される。

## 外部ライブラリと外部サービス

| 種別 | 使用箇所 | 役割 |
|---|---|---|
| Python 3.12 | システム全体 | CLI、規則処理、状態・履歴・ファイル管理 |
| DSPy 3.2.1 | `pooh_narrative_dspy.py`ほか | Signature、Module、Predictor、コンパイル |
| Pydantic 2 | `narrative_state.py`、`mishearing_cases.py` | 物語状態、状態差分、聞き間違い候補の構造化 |
| OpenAI API | `dspy.LM` | 実行時の分類・生成、コンパイル時の生成・評価 |
| ローカルファイル | `.dspy_cache/`、`logs/` | Compiled Agent、キャッシュ、会話ログの保存 |

## 実装との照合

| ファイル | 図との対応 | 確認結果 |
|---|---|---|
| `pooh_narrative_dspy.py` | CLI、3つのPredictor、候補選択、技術語漏出防止、コンパイル、評価、ログ | 一致 |
| `narrative_state.py` | `NarrativeSituation`、`SituationUpdate`、状態差分の適用 | 一致 |
| `pooh_examples.py` | 初期状態、`TRAINSET`、3種類の段階別教師例 | 一致 |
| `mishearing_cases.py` | 固定候補、表記正規化、既知語検索、不確かな短縮音、不理解表現 | 一致 |
| `test_pooh_narrative_dspy.py` | 分類、候補、表記揺れ、評価hard gate、構造化状態更新の回帰テスト | 一致 |
| `README.md` | 環境設定、compile/chat/compare/testの操作方法 | 一致 |

検証には次を使用する。

```bash
python -m unittest test_pooh_narrative_dspy.py
git diff --check
```
