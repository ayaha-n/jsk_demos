"""The fixed public link (scripts/public_link.py): what is sent to Cloudflare,
and what the Worker does with a visit. Nothing reaches Cloudflare."""

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import public_link

CONFIG = {"account_id": "acc", "namespace_id": "ns", "worker": "pooh",
          "link": "https://pooh.story.workers.dev"}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def read(self):
        return json.dumps(self.data).encode() if self.data is not None else b""

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeCloudflare:
    """Records each request; answers by (method, path start)."""

    def __init__(self, answers=None):
        self.requests = []
        self.answers = answers or {}

    def __call__(self, request):
        self.requests.append(request)
        path = request.full_url.removeprefix(public_link.API)
        for (method, prefix), result in self.answers.items():
            if request.get_method() == method and path.startswith(prefix):
                return FakeResponse({"success": True, "result": result})
        return FakeResponse({"success": True, "result": None})


class PublicLinkTest(unittest.TestCase):
    def test_publish_and_withdraw_write_the_scenario_s_tunnel_address_to_kv(self):
        cloudflare = FakeCloudflare()
        public_link.publish("https://a-b-c.trycloudflare.com", "footprints", CONFIG, "secret", cloudflare)
        public_link.withdraw("footprints", CONFIG, "secret", cloudflare)
        put, delete = cloudflare.requests
        self.assertEqual(put.get_method(), "PUT")
        self.assertTrue(put.full_url.endswith(
            "/accounts/acc/storage/kv/namespaces/ns/values/tunnel:footprints"))
        self.assertEqual(public_link.scenario_link(CONFIG, "footprints"),
                         "https://pooh.story.workers.dev/footprints")
        self.assertEqual(put.data, b"https://a-b-c.trycloudflare.com")
        self.assertEqual(put.get_header("Authorization"), "Bearer secret")
        self.assertEqual(delete.get_method(), "DELETE")

    def test_setup_makes_the_worker_and_saves_no_secret(self):
        cloudflare = FakeCloudflare({
            ("GET", "/accounts/acc/storage/kv/namespaces"): [],
            ("POST", "/accounts/acc/storage/kv/namespaces"): {"id": "new-ns"},
            ("GET", "/accounts/acc/workers/subdomain"): {"subdomain": "story"},
        })
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "public_link.json"
            config = public_link.setup("acc", "pooh", "secret", cloudflare, path)
            saved = path.read_text()
        self.assertEqual(config["link"], "https://pooh.story.workers.dev")
        self.assertEqual(config["namespace_id"], "new-ns")
        self.assertNotIn("secret", saved)
        upload = next(r for r in cloudflare.requests if r.get_method() == "PUT")
        self.assertTrue(upload.full_url.endswith("/workers/scripts/pooh"))
        self.assertIn(b'"namespace_id": "new-ns"', upload.data)
        self.assertIn(b'env.LINK.get("tunnel:" + scenario)', upload.data)

    def test_an_api_error_never_shows_the_token(self):
        import urllib.error

        def refuse(request):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},
                                         FakeResponse({"errors": [{"message": "no"}]}))
        with self.assertRaises(public_link.LinkError) as caught:
            public_link.publish("https://x.trycloudflare.com", "footprints", CONFIG, "secret", refuse)
        self.assertNotIn("secret", str(caught.exception))

    def test_the_server_points_and_clears_the_link_only_when_set_up(self):
        import web_server

        calls = []
        with mock.patch.object(public_link, "load_config", return_value=None):
            self.assertIsNone(asyncio.run(
                web_server.point_fixed_link("https://x", "/?token=k", "footprints")))
        with mock.patch.object(public_link, "load_config", return_value=CONFIG), \
                mock.patch.dict("os.environ", {public_link.TOKEN_ENV: "secret", "POOH_WEB_TOKEN": "k"}), \
                mock.patch.object(public_link, "publish",
                                  lambda url, scenario, *a: calls.append(("publish", url, scenario))), \
                mock.patch.object(public_link, "withdraw",
                                  lambda scenario, *a: calls.append(("withdraw", scenario))):
            link = asyncio.run(web_server.point_fixed_link("https://x", "/?token=k", "footprints"))
            asyncio.run(web_server.withdraw_fixed_link(*link))
        self.assertEqual(calls, [("publish", "https://x", "footprints"), ("withdraw", "footprints")])
        with mock.patch.object(public_link, "load_config", return_value=CONFIG), \
                mock.patch.dict("os.environ", {public_link.TOKEN_ENV: ""}):
            self.assertIsNone(asyncio.run(
                web_server.point_fixed_link("https://x", "/?token=k", "footprints")))


@unittest.skipUnless(shutil.which("node"), "node が必要")
class WorkerTest(unittest.TestCase):
    def visit(self, stored, url):
        script = public_link.WORKER_SCRIPT.replace("export default", "const worker =")
        script += f"""
const stored = {json.dumps(stored)};
const env = {{ LINK: {{ get: async key => stored[key] ?? null }} }};
worker.fetch(new Request({json.dumps(url)}), env).then(r =>
  console.log(JSON.stringify({{status: r.status, location: r.headers.get("location"),
                               cache: r.headers.get("cache-control")}})));
"""
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout)

    STORED = {"tunnel:footprints": "https://a-b-c.trycloudflare.com",
              "tunnel:eeyore_birthday": "https://d-e-f.trycloudflare.com"}

    def test_each_scenario_goes_on_to_its_own_tunnel_with_path_and_token(self):
        got = self.visit(self.STORED, "https://pooh.story.workers.dev/footprints/?token=k")
        self.assertEqual(got, {"status": 302, "cache": "no-store",
                               "location": "https://a-b-c.trycloudflare.com/?token=k"})
        got = self.visit(self.STORED, "https://pooh.story.workers.dev/eeyore_birthday/session/1?token=k")
        self.assertEqual(got["location"], "https://d-e-f.trycloudflare.com/session/1?token=k")
        got = self.visit(self.STORED, "https://pooh.story.workers.dev/footprints?token=k")
        self.assertEqual(got["location"], "https://a-b-c.trycloudflare.com/?token=k")

    def test_a_scenario_not_running_or_none_given_says_nothing_is_open(self):
        for url in ("https://pooh.story.workers.dev/tea_party/?token=k",
                    "https://pooh.story.workers.dev/?token=k"):
            self.assertEqual(self.visit(self.STORED, url)["status"], 503)


if __name__ == "__main__":
    unittest.main()
