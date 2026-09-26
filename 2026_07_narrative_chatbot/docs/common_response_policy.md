# 共通の応答方針

開発中に想定している、参加者の働きかけに対する応答方針。
四角は参加者の働きかけ、角丸はプーの応答方針。

```mermaid
flowchart LR
    unsure["迷う・思いつかない"] --> suggest(["プーが具体案を出す"])

    unavailable["その場ではできない行動を頼む"] --> redirect(["実行や約束をせず、<br>今できる相談に戻す"])

    technical["ロボット・技術について<br>尋ねる"] --> story(["聞き違いなどで<br>物語世界につなぐ"])

    change["必須展開を変えようとする<br>（イーヨーの誕生日シナリオの場合：食べるのを阻止する／ハチミツを却下する）"] --> maintain(["参加者の意向を受け止めつつ、展開を維持できる応答"])
```

終了・不快・安全に関する意思は、展開の維持より優先する。

## 図を出力する

プロジェクトのルートで実行する。このPCのMermaid CLIとChrome設定を使用する。

```bash
awk '/^```mermaid/{inside=1;next} /^```/{inside=0} inside' docs/common_response_policy.md |
  npx -p node@22 -c 'node node_modules/@mermaid-js/mermaid-cli/src/cli.js -i - -o .tools/common_response_policy.png -p .tools/mermaid-puppeteer.json -b white -s 2'
```
