"""Study gating, server-side reading workflow and durable code recovery; no LLM."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp.test_utils import AioHTTPTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import web_server
from crowdsourcing import Progress, ProgressStore, book_revision, reached_goal
from test_web_server import FakeOutput, FakeSession


class GoalTests(unittest.TestCase):
    def test_only_story_resolution_or_wrap_up_counts(self):
        state = SimpleNamespace(resolved=False, completed_event_ids={"reveal_hint", "pooh_ate_honey"})
        session = SimpleNamespace(scenario=SimpleNamespace(key="footprints"), controller=SimpleNamespace(state=state))
        self.assertFalse(reached_goal(session))
        state.resolved = True
        self.assertTrue(reached_goal(session))
        session.scenario.key = "eeyore_birthday"
        self.assertFalse(reached_goal(session))
        state.completed_event_ids.add("story_wrap_up")
        self.assertTrue(reached_goal(session))

    def test_receipt_can_be_loaded_by_a_new_store(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = Progress("a" * 32, "footprints", "session_test.jsonl", "start", ended_at="end")
            progress.report("book_opened", "revision")
            with self.assertRaises(ValueError):
                progress.report("read_confirmed", "revision")
            progress.report("last_page", "revision")
            progress.report("read_confirmed", "revision")
            code = progress.completion_code
            self.assertNotIn(progress.session_id, code)
            progress.report("read_confirmed", "revision")
            self.assertEqual(progress.completion_code, code)
            ProgressStore(Path(directory)).save(progress)
            self.assertEqual(ProgressStore(Path(directory)).load("a" * 32), progress)
            with self.assertRaises(ValueError):
                ProgressStore(Path(directory)).load("../secret")


class CrowdsourcingTests(AioHTTPTestCase):
    async def get_application(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.scenario = "footprints"

        def factory(session_id):
            session = FakeSession(session_id)
            session.scenario = SimpleNamespace(key=self.scenario)
            session.log_path = self.directory / f"session_{session_id}.jsonl"
            session.controller = SimpleNamespace(
                state=SimpleNamespace(resolved=False, completed_event_ids=set()),
                required_events=tuple(SimpleNamespace(event_id=name) for name in
                                      ("story_wrap_up", "idle_close_after_reveal", "idle_close_after_wrap_up")),
            )
            return session

        return web_server.create_app(factory, crowdsourcing=True, log_dir=self.directory, access_token="secret")

    @property
    def registry(self):
        return self.app[web_server.REGISTRY_KEY]

    @property
    def headers(self):
        return {web_server.TOKEN_HEADER: "secret"}

    async def start(self):
        response = await self.client.post("/api/sessions", headers=self.headers)
        self.assertEqual(response.status, 200)
        session_id = (await response.json())["session_id"]
        return self.registry.get(session_id)

    async def post_event(self, entry, event, book, **overrides):
        payload = {"event": event, "layout": "landscape", "revision": book_revision(book), **overrides}
        return await self.client.post(f"/api/sessions/{entry.session.session_id}/completion",
                                      json=payload, headers=self.headers)

    async def test_mode_and_goal_are_sent_and_early_button_close_is_rejected(self):
        entry = await self.start()
        self.assertEqual([e.event_id for e in entry.session.controller.required_events], ["story_wrap_up"])
        page = await (await self.client.get("/")).text()
        self.assertIn('data-crowdsourcing="true"', page)
        async with self.client.ws_connect(f"/ws/{entry.session.session_id}", headers=self.headers) as ws:
            transcript = await ws.receive_json()
            self.assertTrue(transcript["study"]["enabled"])
            self.assertFalse(transcript["study"]["goal_reached"])
            await ws.send_json({"type": "close"})
            self.assertEqual((await ws.receive_json())["type"], "error")
            self.assertFalse(entry.session.ended)
            entry.session.controller.state.resolved = True
            await self.registry.run_call(entry, lambda: FakeOutput("world_event", "わかったね"))
            while True:
                message = await asyncio.wait_for(ws.receive_json(), 2)
                if message["type"] == "study_status":
                    self.assertTrue(message["study"]["goal_reached"])
                    break
            await ws.send_json({"type": "close"})
            while (await asyncio.wait_for(ws.receive_json(), 2))["type"] != "ended":
                pass
            self.assertTrue(entry.session.ended)
            self.assertFalse(entry.study.ended_before_goal)

    async def test_early_spoken_exit_can_still_get_code_after_reading(self):
        entry = await self.start()
        # Like a spoken goodbye: the narrative session itself can end before the goal.
        await self.registry.run_call(entry, lambda: entry.session.close("participant_goodbye"))
        self.assertTrue(entry.study.ended_before_goal)
        book = {"title": "絵本", "pages": [{"entries": [], "illustration": None}]}
        with patch.object(web_server, "_storybook_dict", return_value=book):
            response = await self.post_event(entry, "read_confirmed", book)
            self.assertEqual(response.status, 409)
            response = await self.post_event(entry, "book_opened", book)
            self.assertEqual(response.status, 200)
            response = await self.post_event(entry, "read_confirmed", book)
            self.assertEqual(response.status, 409)
            self.assertIsNone((await response.json()).get("completion_code"))
            self.assertEqual((await self.post_event(entry, "last_page", book)).status, 200)
            response = await self.post_event(entry, "read_confirmed", book)
            self.assertEqual(response.status, 200)
            code = (await response.json())["completion_code"]
            self.assertTrue(code)
            repeated = await self.post_event(entry, "read_confirmed", book)
            self.assertEqual((await repeated.json())["completion_code"], code)
        session_id = entry.session.session_id
        saved = self.registry.study_store.load(session_id)
        self.assertTrue(saved.book_opened_at)
        self.assertTrue(saved.last_page_reported_at)
        self.assertTrue(saved.read_confirmed_at)
        self.assertEqual(saved.log_name, entry.session.log_path.name)
        self.registry.discard(session_id)
        # The durable receipt can still be read after the live session expires.
        response = await self.client.get(f"/api/sessions/{session_id}/completion", headers=self.headers)
        self.assertEqual((await response.json())["completion_code"], code)
        self.assertEqual((await self.client.get(f"/api/sessions/{session_id}/completion")).status, 401)

    async def test_reading_requires_ended_session_ready_images_and_current_revision(self):
        entry = await self.start()
        book = {"pages": [{"illustration": "test", "illustration_url": None}]}
        with patch.object(web_server, "_storybook_dict", return_value=book):
            self.assertEqual((await self.post_event(entry, "book_opened", book)).status, 409)
            await self.registry.close(entry)
            self.assertEqual((await self.post_event(entry, "book_opened", book)).status, 409)
            book["pages"][0]["illustration_url"] = "/storybook/test.png"
            self.assertEqual((await self.post_event(entry, "book_opened", book)).status, 200)
            self.assertEqual((await self.post_event(entry, "last_page", book, revision="stale")).status, 409)
            self.assertEqual((await self.post_event(entry, "last_page", book)).status, 200)
            # A later version of the book must itself be viewed through to the end.
            book["title"] = "updated"
            self.assertEqual((await self.post_event(entry, "read_confirmed", book)).status, 409)
            self.assertEqual((await self.post_event(entry, "book_opened", book)).status, 200)
            self.assertIsNone(entry.study.last_page_reported_at)
            self.assertEqual((await self.post_event(entry, "read_confirmed", book)).status, 409)

    async def test_eeyore_waits_for_wrap_up(self):
        self.scenario = "eeyore_birthday"
        entry = await self.start()
        entry.session.controller.state.completed_event_ids.add("story_wrap_up")
        await self.registry.run_call(entry, lambda: FakeOutput("world_event", "よかったね"))
        self.assertTrue(entry.study.goal_reached_at)
        self.assertIsNone(entry.study.completion_code)

    async def test_malformed_reading_event_is_rejected(self):
        entry = await self.start()
        response = await self.client.post(f"/api/sessions/{entry.session.session_id}/completion",
                                          json={"event": []}, headers=self.headers)
        self.assertEqual(response.status, 400)


class RegularModeTests(AioHTTPTestCase):
    async def get_application(self):
        return web_server.create_app(FakeSession)

    async def test_regular_mode_keeps_free_ending_and_no_completion_api(self):
        response = await self.client.post("/api/sessions")
        session_id = (await response.json())["session_id"]
        page = await (await self.client.get("/")).text()
        self.assertIn('data-crowdsourcing="false"', page)
        async with self.client.ws_connect(f"/ws/{session_id}") as ws:
            self.assertFalse((await ws.receive_json())["study"]["enabled"])
            await ws.send_json({"type": "close"})
            while (await asyncio.wait_for(ws.receive_json(), 2))["type"] != "ended":
                pass
        self.assertEqual((await self.client.get(f"/api/sessions/{session_id}/completion")).status, 404)
