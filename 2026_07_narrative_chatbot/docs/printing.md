# ポストカードの印刷（Canon SELPHY CP1500）

ロボット版のダッシュボードでは「ポストカードを印刷」ボタンから開ける。
ロボットのポストカード生成は既定で有効。生成中・失敗時はボタンを押せず、
直前に体験したセッションのPDFが完成すると押せるようになる。
クリックするとGUI内に各カードのプレビューとチェックボックスが並ぶ。
印刷するカードを複数選択し、「選択したカードを印刷」から最終確認へ進む。
プレビューを開いただけでは印刷しない。端末で番号を入力する必要もない。

GUIはチャットボットが書く `.tools/robot_postcards.json` を読み、
生成中に前の体験者のPDFを選ばない。Web版の既定は無効のまま。
チャットボットの配置先を変えたら、`pooh_operator.launch` の
`narrative_chatbot_dir` でGUIとチャットボットの両方に同じ場所を指定する。

ポストカードの PDF（[postcard.md](postcard.md)）を、SELPHY にふちなしで印刷する。同じネットワークにあるPCか、USBケーブルでつないだPCから使える。

```bash
python scripts/print_postcards.py                      # いちばん新しいポストカード: 見本を出して、どのカードを刷るか聞く
python scripts/print_postcards.py logs/postcards/<ログ名>.pdf --cards 1    # 1枚目だけ
python scripts/print_postcards.py --preview-only       # 見本だけ（印刷しない）
```

- 引数なしなら、ログディレクトリの `postcards/` でいちばん新しい PDF を選ぶ。
- まず全部のカードを横に並べた見本の画像を作って開き（画面があるとき）、刷るカード（`1` / `1,3` / `1-3`、Enter で全部）を聞いて、もう一度確かめてから送る。
- プリンターに入っている用紙と PDF のサイズが違えば注意を出す。

## プリンターと用紙

用紙の設定は `config/printer.json`。

| 項目 | 値 |
|---|---|
| プリンター | Canon SELPHY CP1500 |
| 宛先 | プリンター自身。USBでつないでいればそれを使う（`ipp://localhost:60000/ipp/print`、下の「USBでつなぐ」）。なければ `ipp://<プリンターの名前>.local:631/ipp/print`: 初めて使うときに、同じネットワークから名前に「CP1500」を含むプリンターを探し（`ippfind`）、そのPCの `.tools/printer.json`（コミットしない）に覚える。`--printer` で指定もできる |
| L判 | 89 × 119 mm（SELPHY の L。写真店の L判 89×127 mm とは違う）。ふちなし |
| はがき | 100 × 148 mm。ふちなし |

用紙は PDF のページの大きさで決まる。自動で作るポストカード（`--postcards`）は既定で L判。はがきに刷るなら `--postcard-size hagaki` で作る（手で作る `postcard.py` は既定がはがきなので、L判なら `--paper l`）。L判の PDF は、ふちなしで切れないよう四辺に 4 mm あけてある。

## ほかのPCで使うには

- このリポジトリと `.venv`（Pillow を使う）。
- `pdftoppm`・`pdfinfo`（`sudo apt install poppler-utils`）と `ipptool`（`sudo apt install cups-ipp-utils`）。
- `.local` の名前が引けること（Ubuntu なら avahi が既定で入っている）。見つからない・引けなければ `--printer ipp://<プリンターのIPアドレス>:631/ipp/print`。プリンターのアドレスは `ippfind` か、プリンターの画面で確かめる。覚えたアドレスを変えるときは `.tools/printer.json` を消すか書き換える。
- USBでつなぐなら `ippusbxd`（Ubuntu 20.04。`sudo apt install ippusbxd`）か `ipp-usb`（22.04 以降）。
- CUPS にプリンターを登録する必要はない。

## USBでつなぐ

ネットワークで見つからないとき（mDNS が届かない Wi-Fi など）は、USB-C ケーブルでPCにつなぐ。2026-11-10 に確かめたこと。

- SELPHY CP1500 は USB で IPP over USB（インターフェースクラス 7/1/4）に対応している。Ubuntu では、つなぐと udev が `ippusbxd` を起動し、プリンターを `ipp://localhost:60000/ipp/print` で受け付ける（2台目以降は 60001, …）。ネットワークのときと同じく PWG ラスターを受け付け、入っている用紙も答える。
- `print_postcards.py` は、まず localhost の 60000〜60009 番に名前に「CP1500」を含むプリンターがいないか確かめ、いればそれに送る。USB の宛先は差した順で番号が変わるので `.tools/printer.json` には覚えない（覚えてあるネットワークの宛先より USB を先に使う）。
- `ippusbxd` がない環境（Ubuntu 22.04 以降は `ipp-usb`）でも、localhost の同じ範囲で受け付けていれば同じように見つかる。

## なぜ CUPS のキュー（lp）を通さないか

2026-11-09 に確かめたこと。

- `lp -d Canon_SELPHY_CP1500 ...`（CUPS が自動で作ったキュー）は、PDF をプリンター用の画像（Apple の URF）に変える段階で、どのPDFでも「サポートされていないラスターデータです」（`rastertopwg`）で失敗する。`print-scaling` を変えても、紙と同じ大きさ・向きにしても同じ。
- 紙と同じ大きさの JPEG を同じキューに送ると、CUPS は通すが、プリンターが「画像が読み込めない」と断る。
- ブラウザから印刷すると刷れるが、紙いっぱいに広げて刷るので、左右が切れる。

プリンター自身は `image/pwg-raster`（300 dpi、rgb_8）を受け付けると答える。そこで `print_postcards.py` は、各カードを 300 dpi で紙ちょうどの大きさ（L判なら 1051 × 1406 px、紙の向きに合わせて回す）に描き、PWG ラスター（PWG 5102.4）に自分で書き出して、ふちなしの用紙（`media-col`、余白0）を指定して `ipptool` で直接送る。拡大・縮小はしないので、PDF のとおりに刷れる。
