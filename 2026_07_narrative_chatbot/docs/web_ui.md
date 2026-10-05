# Web UI の公開範囲と外部公開

Web UI の起動方法の要点は README にある。ここでは、どこから何が見られるかと、`--public` での外部公開の詳細をまとめる。

## どこから何が使えるか

| | この PC（`127.0.0.1`） | 同じネットワークの別マシン（`--host 0.0.0.0`） | `--public` のトンネル経由 |
|---|---|---|---|
| 会話 | ○ | ○（トークン） | ○（キー） |
| 自分のセッションの絵本・PDF（「絵本を見る」） | ○ | ○（トークン＋セッションID） | ○（キー＋セッションID） |
| 過去ログ・過去ログの絵本と PDF | ○ | × | × |
| タブレットへの書き込み（`--storybook-eink`） | ○ | × | × |

- 「この PC」かどうかは TCP の接続元アドレスだけで判断する。転送ヘッダ（`X-Forwarded-For` など）は誰でも偽れるので使わない。
- 同じ PC でも、自分の IP アドレス（`http://192.168.x.x:8080/` など）で開くと「別マシン」扱いになる。
- 自分の絵本は、ブラウザだけが知るランダムなセッションID（32文字）で開く。他人のセッションや過去ログの一覧には届かない。セッションはサーバが保持している間（切断後 `--session-ttl`、既定10分）だけ見られる。
- 同じネットワーク（Wi-Fi など）の外からは、`--host 0.0.0.0` だけでは届かない。外から使うなら `--public`（下）か、Tailscale などの VPN を使う。

## 外部公開（--public）

`--public` を付けると、[Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) で Web UI に公開用の HTTPS アドレスを付ける（アカウント不要）。

```bash
python scripts/web_server.py --scenario eeyore_birthday --storybook-eink --public
# Web UI: http://127.0.0.1:8080/?token=<キー>
# 公開URL: https://<ランダムな単語>.trycloudflare.com/?token=<キー>
```

- キーは `POOH_WEB_TOKEN`、未設定なら起動ごとにランダムに作る。表示するのはこの端末だけで、ファイルやログには書かない。
- 公開アドレスは起動ごとにランダムで、サーバを止める（Ctrl+C）とトンネルも止まる。
- トンネル経由でできるのは会話と、自分のセッションの絵本・PDF（「絵本を見る」）だけ。過去ログの一覧・閲覧・過去ログの絵本と PDF は拒否し（403）、「過去ログ」ボタンも出さない。セッションはタブレットに書かない。
- トンネルはサーバに `127.0.0.1` から接続してくるので、IP では外部と区別できない。トンネル専用のポートを割り当て、そのポートに着いた要求を外部として扱う。転送ヘッダは誰でも偽れるので使わない。
- `cloudflared` は PATH か、このディレクトリの `.tools/`（git 対象外）から探す:

  ```bash
  mkdir -p .tools && curl -fsSL -o .tools/cloudflared \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x .tools/cloudflared
  ```

### 公開するときの注意

- URL とキーを知っている人は、この PC の OpenAI API キーで会話できる。キーは参加者にだけ渡し、OpenAI の管理画面で利用上限を決めておく。
- 同時セッションは `--max-sessions`（既定20）まで。外から埋められると本番の参加者が入れなくなる。
- 会話は研究データなので、外部経由で扱うことが同意の範囲や倫理審査の条件に合うか確認する。
- 同じ PC にリバースプロキシ（nginx など）を挟まないこと。接続元がすべて `127.0.0.1` に見え、過去ログの「この PC からのみ」が効かなくなる。
- 決まった少人数だけが使うなら、Tailscale などの VPN で同じネットワークに入るほうが安全。
