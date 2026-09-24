"""Web UI server tests with a fake session; no LLM/API calls."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import WSMsgType
from aiohttp.test_utils import AioHTTPTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import web_server


@dataclass
class FakeOutput:
    source: str
    bot_response: str
    performance_cue: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "bot_response": self.bot_response,
            "performance_cue": self.performance_cue,
        }


@dataclass
class FakeSession:
    session_id: str
    due_at: float | None = None
    ended: bool = False
    fail_next: bool = False
    submit_delay: float = 0.0
    inputs: list[str] = field(default_factory=list)
    closed_reasons: list[str] = field(default_factory=list)
    active_calls: int = 0
    max_active_calls: int = 0
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def _enter(self) -> None:
        with self._guard:
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)

    def _leave(self) -> None:
        with self._guard:
            self.active_calls -= 1

    def start(self):
        return FakeOutput("session_open", "やあ", "opening")

    def submit(self, user_input: str):
        self._enter()
        try:
            time.sleep(self.submit_delay)
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("model failure")
            self.inputs.append(user_input)
            return FakeOutput("participant", f"{user_input}、いいね")
        finally:
            self._leave()

    def seconds_until_due(self):
        if self.ended or self.due_at is None:
            return None
        return max(0.0, self.due_at - time.monotonic())

    def poll(self):
        self._enter()
        try:
            if self.due_at is None or time.monotonic() < self.due_at:
                return None
            self.due_at = None
            return FakeOutput("world_event", "ハチミツをなめちゃった")
        finally:
            self._leave()

    def close(self, reason: str = "requested"):
        if self.ended:
            return None
        self.ended = True
        self.closed_reasons.append(reason)
        return FakeOutput("session_close", "またね", "ending")


class WebServerTests(AioHTTPTestCase):
    async def get_application(self):
        self.sessions: dict[str, FakeSession] = {}

        def factory(session_id: str) -> FakeSession:
            session = FakeSession(session_id)
            self.sessions[session_id] = session
            return session

        return web_server.create_app(
            factory,
            max_sessions=3,
            scenario_title="テストシナリオ",
            scene_intro="テーブルの上には<壺>があります。",
        )

    @property
    def registry(self) -> web_server.SessionRegistry:
        return self.app[web_server.REGISTRY_KEY]

    async def new_session(self) -> str:
        response = await self.client.post("/api/sessions")
        self.assertEqual(response.status, 200)
        return (await response.json())["session_id"]

    async def receive_until(self, ws, message_type: str, timeout: float = 2.0) -> dict:
        while True:
            message = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
            if message["type"] == message_type:
                return message

    async def test_opening_is_replayed_on_connect(self):
        session_id = await self.new_session()
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            transcript = await self.receive_until(ws, "transcript")
        self.assertEqual(transcript["messages"][0]["output"]["performance_cue"], "opening")
        self.assertFalse(transcript["ended"])

    async def test_page_uses_scenario_title(self):
        response = await self.client.get("/")
        body = await response.text()
        self.assertEqual(response.status, 200)
        self.assertIn("<title>テストシナリオ</title>", body)
        self.assertIn("<h1>テストシナリオ</h1>", body)

    async def test_page_embeds_escaped_scene_intro(self):
        body = await (await self.client.get("/")).text()
        self.assertIn(
            '<div class="scene-intro">テーブルの上には&lt;壺&gt;があります。</div>', body
        )
        self.assertNotIn("{{SCENE_INTRO}}", body)

    async def test_sessions_are_isolated(self):
        first = await self.new_session()
        second = await self.new_session()
        async with self.client.ws_connect(f"/ws/{first}") as ws:
            await self.receive_until(ws, "transcript")
            await ws.send_json({"type": "user_input", "text": "こんにちは"})
            output = await self.receive_until(ws, "output")
        self.assertEqual(output["output"]["bot_response"], "こんにちは、いいね")
        self.assertEqual(self.sessions[first].inputs, ["こんにちは"])
        self.assertEqual(self.sessions[second].inputs, [])

    async def test_timed_event_is_pushed_without_input(self):
        session_id = await self.new_session()
        self.sessions[session_id].due_at = time.monotonic() + 0.2
        self.registry.sessions[session_id].wake.set()
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            await self.receive_until(ws, "transcript")
            output = await self.receive_until(ws, "output")
        self.assertEqual(output["output"]["source"], "world_event")

    async def test_calls_for_one_session_are_serialized(self):
        session_id = await self.new_session()
        session = self.sessions[session_id]
        session.submit_delay = 0.2
        session.due_at = time.monotonic() + 0.05
        self.registry.sessions[session_id].wake.set()
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            await self.receive_until(ws, "transcript")
            await ws.send_json({"type": "user_input", "text": "ねえ"})
            sources = set()
            while len(sources) < 2:
                sources.add((await self.receive_until(ws, "output"))["output"]["source"])
        self.assertEqual(sources, {"participant", "world_event"})
        self.assertEqual(session.max_active_calls, 1)

    async def test_calls_across_sessions_run_concurrently(self):
        first = await self.new_session()
        second = await self.new_session()
        counter = {"active": 0, "max": 0}
        guard = threading.Lock()
        for session_id in (first, second):
            session = self.sessions[session_id]
            original = session.submit

            def tracked(text, original=original):
                with guard:
                    counter["active"] += 1
                    counter["max"] = max(counter["max"], counter["active"])
                try:
                    return original(text)
                finally:
                    with guard:
                        counter["active"] -= 1

            session.submit = tracked
            session.submit_delay = 0.3
        async with self.client.ws_connect(f"/ws/{first}") as ws1, \
                self.client.ws_connect(f"/ws/{second}") as ws2:
            await self.receive_until(ws1, "transcript")
            await self.receive_until(ws2, "transcript")
            await ws1.send_json({"type": "user_input", "text": "いち"})
            await ws2.send_json({"type": "user_input", "text": "に"})
            await self.receive_until(ws1, "output")
            await self.receive_until(ws2, "output")
        self.assertEqual(counter["max"], 2)

    async def test_model_error_is_reported_and_session_continues(self):
        session_id = await self.new_session()
        self.sessions[session_id].fail_next = True
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            await self.receive_until(ws, "transcript")
            await ws.send_json({"type": "user_input", "text": "一回目"})
            await self.receive_until(ws, "error")
            await ws.send_json({"type": "user_input", "text": "二回目"})
            output = await self.receive_until(ws, "output")
        self.assertEqual(output["output"]["bot_response"], "二回目、いいね")

    async def test_end_button_plays_ending_once(self):
        session_id = await self.new_session()
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            await self.receive_until(ws, "transcript")
            await ws.send_json({"type": "close"})
            output = await self.receive_until(ws, "output")
            await self.receive_until(ws, "ended")
            await ws.send_json({"type": "close"})
            await ws.send_json({"type": "user_input", "text": "まだいる？"})
        self.assertEqual(output["output"]["performance_cue"], "ending")
        self.assertEqual(self.sessions[session_id].closed_reasons, ["web_close"])
        self.assertEqual(self.sessions[session_id].inputs, [])

    async def test_disconnect_keeps_session_for_reconnect(self):
        session_id = await self.new_session()
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            await self.receive_until(ws, "transcript")
            await ws.send_json({"type": "user_input", "text": "またあとで"})
            await self.receive_until(ws, "output")
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            transcript = await self.receive_until(ws, "transcript")
        kinds = [message["type"] for message in transcript["messages"]]
        self.assertEqual(kinds, ["output", "user_input", "output"])
        self.assertEqual(self.sessions[session_id].closed_reasons, [])

    async def test_ttl_discards_without_ending(self):
        session_id = await self.new_session()
        self.registry.sessions[session_id].last_active -= self.registry.ttl_seconds
        self.assertEqual(self.registry.sweep(), [session_id])
        self.assertEqual(self.sessions[session_id].closed_reasons, [])
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            message = await self.receive_until(ws, "expired")
        self.assertEqual(message["type"], "expired")

    async def test_restart_ends_once_and_frees_slot(self):
        session_id = await self.new_session()
        response = await self.client.delete(f"/api/sessions/{session_id}")
        self.assertEqual(response.status, 200)
        self.assertNotIn(session_id, self.registry.sessions)
        self.assertEqual(self.sessions[session_id].closed_reasons, ["web_close"])
        response = await self.client.delete(f"/api/sessions/{session_id}")
        self.assertEqual(response.status, 404)

    async def test_restart_after_ending_does_not_end_again(self):
        session_id = await self.new_session()
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            await self.receive_until(ws, "transcript")
            await ws.send_json({"type": "close"})
            await self.receive_until(ws, "ended")
        await self.client.delete(f"/api/sessions/{session_id}")
        self.assertEqual(self.sessions[session_id].closed_reasons, ["web_close"])
        self.assertNotIn(session_id, self.registry.sessions)

    async def test_restart_closes_open_sockets(self):
        session_id = await self.new_session()
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            await self.receive_until(ws, "transcript")
            await self.client.delete(f"/api/sessions/{session_id}")
            await self.receive_until(ws, "ended")
            message = await asyncio.wait_for(ws.receive(), timeout=2.0)
        self.assertIn(message.type, (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING))

    async def test_session_limit(self):
        for _ in range(3):
            await self.new_session()
        response = await self.client.post("/api/sessions")
        self.assertEqual(response.status, 503)


class BindSecurityTests(unittest.TestCase):
    def test_loopback_without_token_is_allowed(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertIsNone(web_server.check_bind_security(host, ""))

    def test_public_bind_requires_token(self):
        for host in ("0.0.0.0", "::", "192.168.1.10"):
            self.assertIsNotNone(web_server.check_bind_security(host, ""))
            self.assertIsNone(web_server.check_bind_security(host, "secret"))


class LogPathTests(unittest.TestCase):
    def test_log_path_rejects_traversal_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "session_ok.jsonl").write_text("{}\n", encoding="utf-8")
            self.assertIsNotNone(web_server._resolve_log_path(root, "session_ok.jsonl"))
            self.assertIsNone(web_server._resolve_log_path(root, "../session_ok.jsonl"))
            self.assertIsNone(web_server._resolve_log_path(root, "notes.jsonl"))


class LogLocalAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_accepts_only_loopback_peer_addresses(self):
        for address in ("127.0.0.1", "127.10.20.30", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(web_server._is_loopback_address(address), address)
            self.assertEqual(web_server._local_log_style(address), "")

        for address in (None, "localhost", "0.0.0.0", "192.168.1.20", "::"):
            self.assertFalse(web_server._is_loopback_address(address), address)
            self.assertIn("display: none", web_server._local_log_style(address))

    async def test_remote_peer_cannot_list_logs(self):
        request = SimpleNamespace(remote="192.168.1.20")
        response = await web_server.list_logs(request)
        self.assertEqual(response.status, 403)


class LogViewerTests(AioHTTPTestCase):
    async def get_application(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log_dir = Path(temporary.name)
        first = self.log_dir / "session_first.jsonl"
        second = self.log_dir / "session_second.jsonl"
        first.write_text(json.dumps({
            "timestamp": "2026-09-24T01:00:00+00:00",
            "user_action": "こんにちは",
            "bot_response": "こんにちは。",
            "source": "participant",
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        second.write_text(
            json.dumps({
                "timestamp": "2026-09-24T02:00:00+00:00",
                "bot_response": "ハチミツを味見しよう。",
                "source": "world_event",
            }, ensure_ascii=False) + "\nnot-json\n",
            encoding="utf-8",
        )
        os.utime(first, (1, 1))
        os.utime(second, (2, 2))
        (self.log_dir / "unrelated.jsonl").write_text("{}\n", encoding="utf-8")
        return web_server.create_app(FakeSession, log_dir=self.log_dir)

    async def test_lists_session_logs_newest_first(self):
        body = await (await self.client.get("/api/logs")).json()
        self.assertEqual(
            [item["name"] for item in body["logs"]],
            ["session_second.jsonl", "session_first.jsonl"],
        )

    async def test_reads_records_and_reports_invalid_lines(self):
        response = await self.client.get("/api/logs/session_second.jsonl")
        body = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(body["records"][0]["source"], "world_event")
        self.assertEqual(body["invalid_lines"], 1)
        self.assertFalse(body["truncated"])


class AccessTokenTests(AioHTTPTestCase):
    async def get_application(self):
        return web_server.create_app(FakeSession, access_token="secret")

    async def test_token_required(self):
        response = await self.client.post("/api/sessions")
        self.assertEqual(response.status, 401)
        response = await self.client.post(
            "/api/sessions", headers={web_server.TOKEN_HEADER: "secret"}
        )
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/logs")
        self.assertEqual(response.status, 401)


if __name__ == "__main__":
    unittest.main()
