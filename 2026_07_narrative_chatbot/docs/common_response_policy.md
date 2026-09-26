# 共通の応答方針

開発中に想定している、参加者の働きかけに対する応答方針。
シナリオの進行図とは別に、対人イベントでの実際の応答と比較するために用いる。
以下は順番に通る場面ではなく、働きかけと応答方針の対応を表す。
四角は参加者の働きかけ、角丸はプーの応答方針。
この図では色による必須・任意の区別をしない。

```mermaid
flowchart LR
    unsure["迷う・思いつかない"] --> suggest(["プーが具体案を出す"])

    unavailable["その場ではできない行動を頼む"] --> redirect(["実行や約束をせず、<br>今できる相談に戻す"])

    technical["ロボット・技術について尋ねる"] --> story(["聞き違いなどで<br>物語世界につなぐ"])

    change["必須の展開を変えようとする<br>例：食べるのを阻止する／<br>ハチミツを却下する"] --> maintain(["参加者の意向を受け止めつつ、<br>展開を維持できる応答をする"])
```

終了・不快・安全に関する意思は、展開の維持より優先する。
ハチミツの例はイーヨーの誕生日シナリオの具体例で、他のシナリオでは対応する必須の展開に読み替える。

## 図を出力する

プロジェクトのルートで実行する。このPCのMermaid CLIとChrome設定を使用する。

```bash
awk '/^```mermaid/{inside=1;next} /^```/{inside=0} inside' docs/common_response_policy.md |
  npx -p node@22 -c 'node node_modules/@mermaid-js/mermaid-cli/src/cli.js -i - -o .tools/common_response_policy.png -p .tools/mermaid-puppeteer.json -b white -s 2'
```
