"""--postcards: each ended session's postcards, made in the background, and
how the Web UI offers them (this PC only). No LM or image API is called."""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import AioHTTPTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import web_server
from postcard_service import PostcardMaker, postcard_path
from test_web_server import FakeSession


class Pictures:
    """Stands in for SessionIllustrations: settled once told so."""

    def __init__(self):
        self.done = threading.Event()

    def settled(self, session_id):
        return self.done.is_set()


def ended(source="session_close", mode="narrative"):
    return SimpleNamespace(source=source, interaction_mode=mode)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


class PostcardMakerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log = Path(temporary.name) / "session_x.jsonl"
        self.log.write_text("{}\n")
        self.session = SimpleNamespace(session_id="s1", log_path=self.log)
        self.made = []

    def make(self, log_path, pdf):
        self.made.append((log_path, pdf))
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4")
        return "lm"

    def test_cards_wait_for_the_session_s_pictures_and_are_made_once(self):
        pictures = Pictures()
        maker = PostcardMaker(pictures, make=self.make, poll_sec=0.01)
        try:
            maker(self.session, ended(source="participant"))  # not the end
            self.assertIsNone(maker.status("s1"))
            maker(self.session, ended())
            maker(self.session, ended(mode="exit"))  # the same session again
            time.sleep(0.1)
            self.assertEqual(maker.status("s1").status, "waiting")
            self.assertEqual(self.made, [])
            pictures.done.set()
            self.assertTrue(wait_for(lambda: maker.status("s1").status == "ready"))
        finally:
            maker.close()
        self.assertEqual(self.made, [(self.log, postcard_path(self.log))])
        self.assertEqual(postcard_path(self.log), self.log.parent / "postcards" / "session_x.pdf")

    def test_without_pictures_being_drawn_cards_are_made_right_away(self):
        maker = PostcardMaker(None, make=self.make)
        try:
            maker(self.session, ended(mode="exit"))
            self.assertTrue(wait_for(lambda: maker.status("s1").status == "ready"))
        finally:
            maker.close()

    def test_a_session_closed_before_anything_was_said_has_none(self):
        maker = PostcardMaker(None, make=self.make)
        self.log.unlink()
        maker(self.session, ended())
        maker.close()
        self.assertIsNone(maker.status("s1"))
        self.assertEqual(self.made, [])

    def test_a_failure_is_recorded_not_raised(self):
        def broken(log_path, pdf):
            raise RuntimeError("no luck")
        maker = PostcardMaker(None, make=broken)
        try:
            maker(self.session, ended())
            self.assertTrue(wait_for(lambda: maker.status("s1").status == "failed"))
        finally:
            maker.close()
        self.assertIn("no luck", maker.status("s1").error)

    def test_closing_makes_the_ended_session_s_cards_without_waiting_longer(self):
        # A robot run ends right after its session: the cards are made then.
        maker = PostcardMaker(Pictures(), make=self.make, poll_sec=0.01)
        maker(self.session, ended())
        started = time.monotonic()
        maker.close()
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(self.made, [(self.log, postcard_path(self.log))])
        self.assertEqual(maker.status("s1").status, "ready")


class PostcardsOptionTest(unittest.TestCase):
    """--postcards is a picture-book option: the robot's chat has it too."""

    def listeners(self, *argv):
        import argparse
        import pooh_narrative_dspy as pooh

        parser = argparse.ArgumentParser()
        pooh.add_storybook_arguments(parser)
        listeners = pooh.create_storybook_listeners(parser.parse_args(list(argv)))
        self.addCleanup(lambda: [listener.close() for listener in listeners])
        return listeners

    def test_the_maker_comes_last_and_waits_for_the_pictures(self):
        from storybook_illustrations import SessionIllustrations

        self.assertEqual(self.listeners(), [])
        pictures, maker = self.listeners("--storybook-generate", "--postcards")
        self.assertIsInstance(pictures, SessionIllustrations)
        self.assertIsInstance(maker, PostcardMaker)
        self.assertIs(maker.illustrations, pictures)


class GoalPictures(Pictures):
    """SessionIllustrations at the goal: open scenes flushed, then drawn."""

    def __init__(self):
        super().__init__()
        self.flushed = []

    def flush(self, session_id):
        self.flushed.append(session_id)

    def drawn(self, session_id):
        return bool(self.flushed)


class PostcardsAtTheGoalTest(PostcardMakerTest):
    def goal_session(self, resolved):
        return SimpleNamespace(session_id="s1", log_path=self.log,
                               scenario=SimpleNamespace(key="footprints"),
                               controller=SimpleNamespace(state=SimpleNamespace(resolved=resolved)))

    def test_cards_are_made_once_the_goal_is_reached_without_waiting_for_the_end(self):
        pictures = GoalPictures()  # settled() stays False: the session goes on
        maker = PostcardMaker(pictures, make=self.make, poll_sec=0.01)
        try:
            maker(self.goal_session(False), ended(source="participant"))
            self.assertIsNone(maker.status("s1"))
            maker(self.goal_session(True), ended(source="participant"))
            self.assertEqual(pictures.flushed, ["s1"])
            self.assertTrue(wait_for(lambda: maker.status("s1").status == "ready"))
            maker(self.goal_session(True), ended())  # the end later: not made again
        finally:
            maker.close()
        self.assertEqual(len(self.made), 1)


class ReadyMaker:
    """A maker whose job for session "s1" is done."""

    def __init__(self, pdf):
        self.job = SimpleNamespace(status="ready", pdf=pdf, error="")

    def status(self, session_id):
        return self.job if session_id == "s1" else None


class PostcardEndpointsTest(AioHTTPTestCase):
    async def get_application(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log_dir = Path(temporary.name)
        log = self.log_dir / "session_book.jsonl"
        log.write_text(json.dumps({
            "scenario": "eeyore_birthday", "user_action": "こんにちは",
            "bot_response": "やあ。", "source": "participant",
            "interaction_mode": "narrative"}, ensure_ascii=False) + "\n", encoding="utf-8")
        self.pdf = postcard_path(log)
        self.pdf.parent.mkdir(parents=True)
        self.pdf.write_bytes(b"%PDF-1.4 cards")
        return web_server.create_app(FakeSession, log_dir=self.log_dir,
                                     postcards=ReadyMaker(self.pdf))

    async def test_a_session_s_cards_once_ready(self):
        body = await (await self.client.get("/api/sessions/s1/postcards")).json()
        self.assertEqual(body, {"status": "ready", "url": "/api/sessions/s1/postcards.pdf"})
        response = await self.client.get("/api/sessions/s1/postcards.pdf")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"%PDF-1.4 cards")
        body = await (await self.client.get("/api/sessions/other/postcards")).json()
        self.assertEqual(body, {"status": "none"})

    async def test_past_logs_offer_the_cards_beside_the_book(self):
        book = await (await self.client.get("/api/logs/session_book.jsonl/storybook")).json()
        self.assertEqual(book["postcards_url"], "/api/logs/session_book.jsonl/postcards.pdf")
        response = await self.client.get(book["postcards_url"])
        self.assertEqual(await response.read(), b"%PDF-1.4 cards")

    async def test_not_through_the_public_tunnel(self):
        port = self.client.server.port
        self.app[web_server.PUBLIC_PORT_KEY] = port  # every request now counts as public
        for path in ("/api/sessions/s1/postcards", "/api/sessions/s1/postcards.pdf",
                     "/api/logs/session_book.jsonl/postcards.pdf"):
            self.assertEqual((await self.client.get(path)).status, 403, path)


class PostcardsOffTest(AioHTTPTestCase):
    async def get_application(self):
        return web_server.create_app(FakeSession)

    async def test_without_the_option_there_are_none(self):
        body = await (await self.client.get("/api/sessions/s1/postcards")).json()
        self.assertEqual(body, {"status": "off"})


if __name__ == "__main__":
    unittest.main()
