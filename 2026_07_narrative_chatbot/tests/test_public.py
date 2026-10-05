"""--public: the tunnel, and what tunnelled requests may reach.

A stand-in script plays cloudflared, so nothing is ever published.
"""

import asyncio
import json
import os
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import public_tunnel
import web_server
from test_web_server import FakeSession

FAKE_URL = "https://quiet-forest-path.trycloudflare.com"


def fake_cloudflared(directory: Path, *, print_url: bool = True, lifetime: str = "300") -> str:
    """A cloudflared that logs a quick-tunnel address and waits, recording
    the port it was pointed at."""
    script = directory / "cloudflared"
    lines = [
        "#!/bin/sh",
        f'echo "$@" > "{directory}/args"',
        'echo "INF Requesting new quick Tunnel" >&2',
    ]
    if print_url:
        lines.append(f'echo "INF |  {FAKE_URL}  |" >&2')
        # exec keeps the pid, so the recorded one is the process to end.
        lines.append(f'echo $$ > "{directory}/pid"')
        lines.append(f"exec sleep {lifetime}")
    script.write_text("\n".join(lines) + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


class QuickTunnelTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_reads_the_address_and_stop_ends_the_process(self):
        with tempfile.TemporaryDirectory() as directory:
            tunnel = public_tunnel.QuickTunnel(fake_cloudflared(Path(directory)))
            self.assertEqual(await tunnel.start(4321), FAKE_URL)
            process = tunnel.process
            self.assertIn("--url http://127.0.0.1:4321", (Path(directory) / "args").read_text())
            await tunnel.stop()
            self.assertIsNotNone(process.returncode)
            await tunnel.stop()  # idempotent

    async def test_cloudflared_exiting_without_an_address_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            tunnel = public_tunnel.QuickTunnel(fake_cloudflared(Path(directory), print_url=False))
            with self.assertRaises(RuntimeError):
                await tunnel.start(4321)


class TunnelDyingTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_stops_with_an_error_when_the_tunnel_ends(self):
        with tempfile.TemporaryDirectory() as directory:
            app = web_server.create_app(FakeSession, access_token="key")
            port = free_port()
            serving = web_server.serve_public(
                app, "127.0.0.1", port, fake_cloudflared(Path(directory), lifetime="0.5"), "/"
            )
            with self.assertRaises(RuntimeError) as raised:
                await asyncio.wait_for(serving, 20)
            self.assertIn("公開トンネル", str(raised.exception))
            # The server is down too, not left serving unpublished.
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=1).close()


class ViaTunnelTests(unittest.TestCase):
    def request(self, local_port: int, public_port: int | None):
        app = {} if public_port is None else {web_server.PUBLIC_PORT_KEY: public_port}
        transport = SimpleNamespace(get_extra_info=lambda key: ("127.0.0.1", local_port))
        return SimpleNamespace(app=app, transport=transport, remote="127.0.0.1")

    def test_told_apart_by_the_port_not_the_peer(self):
        self.assertTrue(web_server._via_public_tunnel(self.request(5555, 5555)))
        self.assertFalse(web_server._is_local_request(self.request(5555, 5555)))
        self.assertFalse(web_server._via_public_tunnel(self.request(8080, 5555)))
        self.assertTrue(web_server._is_local_request(self.request(8080, 5555)))
        self.assertTrue(web_server._is_local_request(self.request(8080, None)))


class AccessKeyTests(unittest.TestCase):
    def test_public_without_a_token_gets_a_fresh_random_key(self):
        first = web_server.resolve_access_token("", public=True)
        second = web_server.resolve_access_token("", public=True)
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 32)

    def test_a_configured_token_is_used_as_is(self):
        self.assertEqual(web_server.resolve_access_token("mine", public=True), "mine")

    def test_normal_launch_is_unchanged(self):
        self.assertEqual(web_server.resolve_access_token("", public=False), "")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ServePublicTests(unittest.IsolatedAsyncioTestCase):
    """The real serve_public, with the stand-in cloudflared."""

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.log_dir = root / "logs"
        self.log_dir.mkdir()
        (self.log_dir / "session_past.jsonl").write_text(json.dumps({
            "scenario": "eeyore_birthday", "source": "participant",
            "user_action": "ないしょ", "bot_response": "うん。", "interaction_mode": "narrative",
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        self.tablet = object()

        def factory(session_id: str) -> FakeSession:
            session = FakeSession(session_id)
            session.log_path = self.log_dir / f"session_x_{session_id[:8]}.jsonl"
            session.scenario = SimpleNamespace(key="eeyore_birthday")
            return session

        self.app = web_server.create_app(
            factory, log_dir=self.log_dir, access_token="key", local_output_listeners=[self.tablet]
        )
        self.port = free_port()
        self.server = asyncio.create_task(web_server.serve_public(
            self.app, "127.0.0.1", self.port, fake_cloudflared(root), "/?token=key"
        ))
        for _ in range(100):
            if web_server.PUBLIC_PORT_KEY in self.app and (root / "args").exists():
                break
            await asyncio.sleep(0.05)
        self.public_port = self.app[web_server.PUBLIC_PORT_KEY]
        self.http = aiohttp.ClientSession(headers={web_server.TOKEN_HEADER: "key"})

    async def asyncTearDown(self):
        await self.http.close()
        self.server.cancel()
        try:
            await self.server
        except asyncio.CancelledError:
            pass
        self.directory.cleanup()

    async def get(self, port: int, path: str):
        async with self.http.get(f"http://127.0.0.1:{port}{path}") as response:
            return response.status, await response.read()

    async def test_tunnel_gets_its_own_port(self):
        args = (Path(self.directory.name) / "args").read_text()
        self.assertIn(f"--url http://127.0.0.1:{self.public_port}", args)
        self.assertNotEqual(self.public_port, self.port)

    async def test_past_logs_are_refused_through_the_tunnel_only(self):
        for path in ("/api/logs", "/api/logs/session_past.jsonl",
                     "/api/logs/session_past.jsonl/storybook",
                     "/api/logs/session_past.jsonl/storybook.pdf"):
            self.assertEqual((await self.get(self.public_port, path))[0], 403, path)
        self.assertEqual((await self.get(self.port, "/api/logs"))[0], 200)

    async def test_page_through_the_tunnel_hides_the_past_log_viewer(self):
        _, body = await self.get(self.public_port, "/")
        self.assertIn(b".local-log-feature { display: none", body)
        _, body = await self.get(self.port, "/")
        self.assertNotIn(b".local-log-feature { display: none", body)

    async def test_tunnelled_session_has_its_own_book_but_not_the_tablet(self):
        async with self.http.post(f"http://127.0.0.1:{self.public_port}/api/sessions") as response:
            session_id = (await response.json())["session_id"]
        entry = self.app[web_server.REGISTRY_KEY].get(session_id)
        self.assertEqual(getattr(entry.session, "output_listeners", ()), ())
        status, _ = await self.get(self.public_port, f"/api/sessions/{session_id}/storybook")
        self.assertEqual(status, 200)
        async with self.http.post(f"http://127.0.0.1:{self.port}/api/sessions") as response:
            local_id = (await response.json())["session_id"]
        local = self.app[web_server.REGISTRY_KEY].get(local_id)
        self.assertEqual(local.session.output_listeners, (self.tablet,))

    async def test_key_is_still_required_through_the_tunnel(self):
        async with aiohttp.ClientSession() as anonymous:
            async with anonymous.post(f"http://127.0.0.1:{self.public_port}/api/sessions") as response:
                self.assertEqual(response.status, 401)

    async def test_stopping_the_server_ends_the_tunnel(self):
        pid_file = Path(self.directory.name) / "pid"
        for _ in range(50):
            if pid_file.exists() and pid_file.read_text().strip():
                break
            await asyncio.sleep(0.05)
        pid = pid_file.read_text().strip()
        self.assertTrue(os.path.exists(f"/proc/{pid}"))
        self.server.cancel()
        try:
            await self.server
        except asyncio.CancelledError:
            pass
        for _ in range(50):
            if not os.path.exists(f"/proc/{pid}"):
                break
            await asyncio.sleep(0.05)
        self.assertFalse(os.path.exists(f"/proc/{pid}"))
        with self.assertRaises(aiohttp.ClientError):
            await self.get(self.public_port, "/")


if __name__ == "__main__":
    unittest.main()
