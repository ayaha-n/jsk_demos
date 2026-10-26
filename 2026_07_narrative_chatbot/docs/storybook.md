# 絵本

セッションを絵本にして reMarkable Paper Pro にライブで書き、あとから読み返せるようにする機能の詳細。起動方法の要点は README にある。Web UI を外から使う `--public` は [web_ui.md](web_ui.md)。

## 絵本の中身

絵本は、セッションが記録したことだけを語り直す。モデルに地の文を作らせることはしない。

- 参加者の言葉（「…」と、あなたは いいました。）
- プーのセリフ
- 固定セリフに付いた体の動き（プーは かなしそうな かおで、あごに てを あてて、…）
- 時間経過で起きる出来事（それぞれが新しい場面になる）

ページは場面の区切りと、文字が入りきらないところで変わる。場面ごとの地の文・挿絵・挿絵の生成プロンプトと、動作の言い回しは `config/storybook.json` で設定する。動作の言い回しは、ロボット側（pooh_body）の動作名すべてに用意してある。

### ページの組み方

- タブレットの向きはセッション開始時に読む。縦は挿絵が上・文字が下、横は見開き（左に文字、右に挿絵）。
- 挿絵のない場面転換は、横向きで右ページが空いていれば、ページをめくらず右ページから続ける。
- 挿絵ファイルがない場面は文字だけのページになる。
- 会話から挿絵を生成する場面（`--storybook-generate`）は、挿絵はその場面が終わってから描かれるので、待たずにページを始めて挿絵の枠を空けたまま文字を書く。次の場面が始まるか本が終わるときに挿絵を待ち（最大90秒、`scripts/storybook_live.py` の `ILLUSTRATION_WAIT_SEC`）、届いたら枠に描き足してからめくる。間に合わなければ、そのときある挿絵（なければ空けたまま）で先に進み、ログに出す。あとで届いた挿絵も PDF には入る。
- 長いセリフはページや段をまたいで続き、切り捨てない。
- 文字は viewer と同じ Yomogi フォント・同じ大きさで測って折り返すので、タブレット上で折り返し位置がずれない。

## タブレット側（storybook_viewer）

`storybook_viewer/` は AppLoad アプリで、[plush_memory](https://github.com/ayaha-n/plush_memory) の e-ink viewer をライブラリとして使い（plush_memory README の "Reusing the viewer in another app"）、書き終えた絵本の読み返しだけを足している。PC からは `plush_memory/scripts/eink_memory_push.py` で ssh 越しに送る。タブレットには plush_memory と同じく xovi と AppLoad が必要。

一度だけビルドして入れる:

```bash
# 2026_07_narrative_chatbot/ で
(cd storybook_viewer && cargo build --release --target aarch64-unknown-linux-musl)
ssh root@10.11.99.1 mkdir -p /home/root/xovi/exthome/appload/storybook_viewer
scp storybook_viewer/target/aarch64-unknown-linux-musl/release/storybook_viewer \
    storybook_viewer/external.manifest.json storybook_viewer/icon.png \
    root@10.11.99.1:/home/root/xovi/exthome/appload/storybook_viewer/
# タブレットを横に置く場合（xochitl の自動回転は切る）:
ssh root@10.11.99.1 'echo landscape_cw > /home/root/xovi/exthome/appload/storybook_viewer/orientation.conf'
```

AppLoad の一覧で再読み込みし、ほかの AppLoad アプリを閉じて "Storybook" を開いてから、チャットを `--storybook-eink` 付きで起動する（README の Picture book）。

- タブレットまわり（ライブ書き込みと `storybook_viewer` のビルド）だけ [plush_memory](https://github.com/ayaha-n/plush_memory)（当面は `feature/eink-display` ブランチ）が要る。`jsk_demos` と同じ catkin ワークスペースの `src/` にある前提（`~/catkin_ws/src/plush_memory`）。別の場所にあるなら、PC 側（Python、`eink_memory_push.py`）は `PLUSH_MEMORY_DIR` で指定し、タブレット側のビルドは `storybook_viewer/Cargo.toml` の `path = "../../../plush_memory/eink-viewer"` を書き換える。
- 手書きフォント Yomogi は `assets/fonts/` に同梱している（plush_memory の `eink-viewer/fonts/` と同じファイル。由来と SHA256 は `assets/fonts/README.md`、一致はテストで確認）。PDF と Web の絵本は plush_memory なしで動く。
- タブレットの宛先は `EINK_HOST`（既定 `10.11.99.1`、USB）。
- タブレットに届かなくても会話は続き、エラーが表示されるだけ。
- 会話に書くのが追いつかないとき（タブレットの未描画が溜まったとき）は書く速さを上げる。
- ページをめくる前に、タブレットが今のページを描き終えるのを待ち、3秒読ませてから白で消して3秒置く（前のページのゴースト対策）。秒数は `scripts/storybook_live.py` の定数。
- PC 側が送信の途中で止まると、`READY` のない event フォルダがタブレットに残る。viewer はこれを描かないので未描画には数えず、次の本の始まりに消す。

### どのセッションをタブレットに書くか

- ロボットモード（`pooh_narrative_dspy.py --ros-relay`）はこの PC で動くので、常に書く。
- Web UI では、この PC 自身のブラウザ（`127.0.0.1`）から始めたセッションだけを書く。ほかのマシン（同じ PC でも自分の IP アドレスで開いた場合を含む）や `--public` 経由のセッションは書かない。
- この PC から同時に複数のセッションがあれば、最後に始まったものを書く。それまで書いていた本は「おしまい」で閉じ、PDF は保存しない（そのセッションは自分のブラウザで続いているので、途中で切れた PDF を残さないため）。

### 表紙

本の最後に表紙を足して閉じるので、次の本が始まるまでタブレットには表紙が出ている。PDF では1ページ目（ページ番号なし）。

- plush_memory の表紙にならい、手描き風の二重枠の中に、上に題名（太めのペン）、下に表紙の絵、右下に「めくる→」を書く。表紙で右をタップすると1ページ目に戻る。
- 表紙の絵は `config/storybook.json` のシナリオの `cover`（`illustration`）。イーヨーの話は、机にひじをついて考えるプーの吹き出しに、その回の締めくくりの場面の挿絵（`bubble_beat`。締めくくりまで進まなかったときや描けなかったときは、同梱のシンプルな締めくくりの絵）を描き込む（`assets/storybook/eeyore_birthday/cover.png`、プロンプトは `cover.prompt.txt`）。`bubble` は吹き出しの中の1点で、その白い領域に、ふちを少し（`COVER_BUBBLE_OVERFLOW`）はみ出すまで大きく描く。
- 合成した表紙は `assets/storybook/generated/cover-<ハッシュ>.png` に一度だけ作る。作るのはライブの本の終わりだけで、Web や PDF をログから作るときは、作ってあればそれを使い、なければ空の吹き出しのまま出す。

### タブレットでの読み返し

本を書き終えると読み返しモードになる。

- 右1/3 をタップで次のページ、左1/3 で前のページ。最後のページの次は最初、最初の前は最後に戻る。
- めくるときは白で消してから描く。消してから描くまでの待ち時間は `scripts/storybook_live.py` の `PAGE_TURN_CLEAR_MS`（既定1秒）で、本の終わりに PC から送るので viewer の再ビルドはいらない。めくり中のタップは捨てる。
- 本を書いている間はタッチしても何も起きない。次のセッションの本が始まると読み返しは終わる。
- 保存しておくページは最大40枚（1枚が画面全体で約7MB）。

## 保存と見返し

ライブの本は終わったときに、タブレットに出したとおりの向き・組みで PDF として保存する（ログディレクトリの `storybooks/<ログ名>.pdf`、既定は `logs/storybooks/`）。PDF の文字は手書きフォントの文字として入るので、拡大しても崩れず検索もできる。画像は挿絵だけ。

| 見返し方 | 使い方 | 見られる範囲 |
|---|---|---|
| タブレット | 書き終えたら左右をタップ | 直前の本 |
| Web UI「絵本を見る」 | セッション終了後に出るボタン。横/縦と PDF リンクつき | そのブラウザのセッションの本だけ。トークンとセッションIDが要る。外部や `--public` 経由からも見られる（[web_ui.md](web_ui.md)） |
| Web UI「過去ログ」→「絵本で見る」 | 過去ログを選んで切り替え。横/縦と PDF リンクつき | すべてのログ。この PC からのみ |
| ファイル | 下のコマンド | 任意のログ |

```bash
python scripts/storybook_render.py logs/session_<...>.jsonl          # -> logs/storybooks/<ログ名>.pdf
python scripts/storybook_render.py logs/saved_examples/<...>.jsonl --layout portrait --out /tmp/book.pdf --png /tmp/pages
```

PDF リンクは、画面で選んだ向きの PDF をその場で作って返す。向きを指定しない `.../storybook.pdf` は、ライブで保存した PDF があればそれを返す。

`--storybook-preview DIR` を付けると、書いているページを PNG として DIR にも書き出す（タブレットなしでの確認用。`--storybook-eink` と併用もできる）。

## 挿絵

挿絵は `assets/storybook/<シナリオ>/` の PNG で、`config/storybook.json` に書いたパスで読む。リポジトリを軽くするため 256色 PNG にしてある（見た目の差は小さく、タブレットの描き出しは白黒）。タブレットに送るときは RGBA に直してから送る（`eink_memory_push` は透明部分から切り抜くため）。

`scripts/storybook_illustrations.py --scenario <key>` は、各場面のプロンプトから `gpt-image-1.5` で挿絵を描かせる。参照画像は `assets/storybook/bear-reference.png`。ただし画像モデルのモデレーションで出力段階で拒否されることがあり（`moderation_blocked`）、その場合は手で描いた絵や写真を置く。参加者は顔や体を描かず、画面の下から差す影として表す。物を手渡したり差し出したり、プーに触れたりした場面では、手と腕だけを画面の端から描いてよい（`config/storybook.json` の `participant_presence`）。会話中に生成する挿絵の要約には、ROS 側が見た参加者の行為（source `observed`）も会話と同じ時系列で `reader_did` として渡す。
