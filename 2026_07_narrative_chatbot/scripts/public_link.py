#!/usr/bin/env python3
"""A fixed public link for the Web UI that forwards to its current quick tunnel.

``--public``'s quick tunnel gets a new random address each time the server
starts. This keeps one address per scenario that does not change -- a
Cloudflare Worker on ``<name>.<subdomain>.workers.dev`` that forwards
``/<scenario>/...`` (the rest of the path and ``?token=`` kept) to the tunnel
of the server running that scenario now. The Worker reads each scenario's
address from Workers KV; ``web_server.py --public`` writes its own scenario's
there when the tunnel comes up and removes it when the server stops, so
servers for different scenarios can run side by side. No account name of
anyone shows in the link.

Once (needs a free Cloudflare account and an API token, see docs/web_ui.md)::

    CLOUDFLARE_API_TOKEN=... python scripts/public_link.py setup --account-id <id> --name pooh
    # -> https://pooh.<subdomain>.workers.dev/<scenario>/?token=...

That creates the KV namespace, uploads the Worker and turns on its
workers.dev address, and saves what the server needs (no secret) in
``.tools/public_link.json``. Then ``web_server.py --public`` with
``CLOUDFLARE_API_TOKEN`` set also prints the fixed link. Keep
``POOH_WEB_TOKEN`` fixed too, or the link's ``?token=`` changes anyway.

Workers KV serves a cached value for up to about a minute, so right after a
restart a visit may still be forwarded to the old address for a little while.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / ".tools" / "public_link.json"
API = "https://api.cloudflare.com/client/v4"
TOKEN_ENV = "CLOUDFLARE_API_TOKEN"
KV_PREFIX = "tunnel:"  # one KV key per scenario: tunnel:<scenario>
KV_TITLE = "pooh-public-link"
COMPATIBILITY_DATE = "2024-09-01"

# The Worker: /<scenario>/<rest>?<query> goes to that scenario's tunnel
# as /<rest>?<query> (so ?token=... comes along); never cached, so a
# restart's new address takes over.
WORKER_SCRIPT = """\
export default {
  async fetch(request, env) {
    const headers = { "cache-control": "no-store" };
    const url = new URL(request.url);
    const [, scenario = "", ...rest] = url.pathname.split("/");
    const target = scenario && await env.LINK.get("tunnel:" + scenario);
    if (!target) {
      return new Response(
        "いまは公開していません。しばらくしてから、もう一度開いてください。",
        { status: 503, headers: { ...headers, "content-type": "text/plain; charset=utf-8" } });
    }
    const destination = new URL("/" + rest.join("/") + url.search, target);
    return new Response(null, { status: 302, headers: { ...headers, location: destination.toString() } });
  },
};
"""

Opener = Callable[[urllib.request.Request], Any]


class LinkError(RuntimeError):
    pass


def _call(method: str, path: str, token: str, body: bytes | None = None,
          content_type: str | None = None, opener: Opener | None = None) -> Any:
    request = urllib.request.Request(f"{API}{path}", data=body, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    if content_type:
        request.add_header("Content-Type", content_type)
    try:
        with (opener or (lambda r: urllib.request.urlopen(r, timeout=20)))(request) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            errors = json.loads(raw).get("errors")
        except ValueError:
            errors = None
        # The API's own error messages, never the token.
        raise LinkError(f"Cloudflare API {method} {path}: {exc.code} {errors or ''}".strip()) from None
    if not raw:
        return None
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("success") is False:
        raise LinkError(f"Cloudflare API {method} {path}: {data.get('errors')}")
    return data.get("result") if isinstance(data, dict) else data


def load_config(path: Path = CONFIG_PATH) -> dict | None:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    needed = ("account_id", "namespace_id", "link")
    return config if isinstance(config, dict) and all(config.get(k) for k in needed) else None


def scenario_link(config: dict, scenario: str) -> str:
    """The fixed link of a scenario (without ?token=)."""
    return f"{config['link']}/{scenario}"


def _value_path(config: dict, scenario: str) -> str:
    return (f"/accounts/{config['account_id']}/storage/kv/namespaces/"
            f"{config['namespace_id']}/values/{KV_PREFIX}{scenario}")


def publish(tunnel_url: str, scenario: str, config: dict, token: str,
            opener: Opener | None = None) -> None:
    """Point the scenario's fixed link at this tunnel."""
    _call("PUT", _value_path(config, scenario), token, tunnel_url.encode(), "text/plain", opener)


def withdraw(scenario: str, config: dict, token: str, opener: Opener | None = None) -> None:
    """Point the scenario's fixed link at nothing (it then says nothing is open)."""
    _call("DELETE", _value_path(config, scenario), token, opener=opener)


def _multipart(parts: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    """(name, filename, content, content type) parts as multipart/form-data."""
    boundary = uuid.uuid4().hex
    body = b""
    for name, filename, content, content_type in parts:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n").encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def setup(account_id: str, name: str, token: str, opener: Opener | None = None,
          path: Path = CONFIG_PATH) -> dict:
    """Create the KV namespace (or reuse it), upload the Worker bound to it,
    turn on its workers.dev address, and save the settings (no secret)."""
    namespaces = _call("GET", f"/accounts/{account_id}/storage/kv/namespaces?per_page=100",
                       token, opener=opener) or []
    namespace_id = next((ns["id"] for ns in namespaces if ns.get("title") == KV_TITLE), None)
    if namespace_id is None:
        created = _call("POST", f"/accounts/{account_id}/storage/kv/namespaces", token,
                        json.dumps({"title": KV_TITLE}).encode(), "application/json", opener)
        namespace_id = created["id"]
    metadata = {
        "main_module": "worker.js",
        "compatibility_date": COMPATIBILITY_DATE,
        "bindings": [{"type": "kv_namespace", "name": "LINK", "namespace_id": namespace_id}],
    }
    body, content_type = _multipart([
        ("metadata", "metadata.json", json.dumps(metadata).encode(), "application/json"),
        ("worker.js", "worker.js", WORKER_SCRIPT.encode(), "application/javascript+module"),
    ])
    _call("PUT", f"/accounts/{account_id}/workers/scripts/{name}", token, body, content_type, opener)
    _call("POST", f"/accounts/{account_id}/workers/scripts/{name}/subdomain", token,
          json.dumps({"enabled": True}).encode(), "application/json", opener)
    subdomain = (_call("GET", f"/accounts/{account_id}/workers/subdomain", token,
                       opener=opener) or {}).get("subdomain")
    if not subdomain:
        raise LinkError("workers.dev のサブドメインがまだありません。"
                        "Cloudflare の管理画面（Workers & Pages）で一度決めてください。")
    config = {"account_id": account_id, "namespace_id": namespace_id, "worker": name,
              "link": f"https://{name}.{subdomain}.workers.dev"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Web UI の固定リンク（Cloudflare Worker）")
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("setup", help="KV・Worker を作り、設定を .tools/public_link.json に保存する")
    make.add_argument("--account-id", required=True)
    make.add_argument("--name", default="pooh", help="Worker の名前（リンクの先頭、既定 pooh）")
    off = sub.add_parser("off", help="シナリオの固定リンクの行き先を消す（「公開していません」と出す）")
    off.add_argument("scenario")
    args = parser.parse_args()
    token = os.getenv(TOKEN_ENV, "")
    if not token:
        print(f"{TOKEN_ENV} を設定してください。", file=sys.stderr)
        return 1
    try:
        if args.command == "setup":
            config = setup(args.account_id, args.name, token)
            print(f"固定リンク: {config['link']}/<シナリオ>/?token=<POOH_WEB_TOKEN>"
                  f"（例: {scenario_link(config, 'footprints')}/?token=…）")
            print(f"設定を保存しました: {CONFIG_PATH}")
        else:
            config = load_config()
            if config is None:
                print("先に setup してください。", file=sys.stderr)
                return 1
            withdraw(args.scenario, config, token)
            print(f"{scenario_link(config, args.scenario)} の行き先を消しました。")
    except (LinkError, urllib.error.URLError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
