"""Corrections to what the speech recognizer heard: kept beside the log, and
taken up by the books made from it, the live book and the tablet.

No tablet, LLM or image API is used.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp.test_utils import AioHTTPTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import storybook
import storybook_corrections
import storybook_editor
import storybook_live
from storybook import LAYOUTS, StoryBuilder, StoryTurn
from storybook_corrections import Correction
from storybook_render import build_from_log, read_log
from test_storybook import EEYORE, FakeBackend, fake_output, fake_session, full_width


def write_log(directory: Path, records: list[dict]) -> Path:
    path = directory / "session_20261115T000000_abcdefgh.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")
    return path


RECORDS = [
    {"scenario": "eeyore_birthday", "session_id": "s" * 32, "source": "participant",
     "user_action": "イオンの誕生日", "bot_response": "そうなんだ。", "interaction_mode": "narrative"},
    {"scenario": "eeyore_birthday", "session_id": "s" * 32, "source": "observed",
     "observed_actions": ["参加者が風船をプーに手渡した"], "with_speech": True},
    {"scenario": "eeyore_birthday", "session_id": "s" * 32, "source": "participant",
     "user_action": "「どう ぞ」と言って、風船をプーに手渡す", "bot_response": "ありがとう。",
     "interaction_mode": "narrative"},
    {"scenario": "eeyore_birthday", "session_id": "s" * 32, "source": "story_goal"},
    {"scenario": "eeyore_birthday", "session_id": "s" * 32, "source": "participant",
     "user_action": "たのしかった", "bot_response": "うん。", "interaction_mode": "narrative"},
]


class CorrectionsFileTest(unittest.TestCase):
    def test_utterances_are_numbered_and_only_what_was_said_is_corrected(self):
        found = storybook_corrections.utterances(RECORDS)
        self.assertEqual([(u.utterance, u.record_index, u.said) for u in found],
                         [(0, 0, "イオンの誕生日"), (1, 2, "どう ぞ"), (2, 4, "たのしかった")])
        corrected = storybook_corrections.apply_corrections(RECORDS, {
            0: Correction(0, "イオンの誕生日", "イーヨーの誕生日"),
            1: Correction(1, "「どう ぞ」と言って、風船をプーに手渡す", "どうぞ"),
        })
        self.assertEqual(corrected[0]["user_action"], "イーヨーの誕生日")
        self.assertEqual(corrected[0]["heard_user_action"], "イオンの誕生日")
        self.assertEqual(corrected[2]["user_action"], "「どうぞ」と言って、風船をプーに手渡す")
        self.assertEqual(corrected[4], RECORDS[4])
        self.assertEqual(RECORDS[0]["user_action"], "イオンの誕生日")  # not changed in place

    def test_a_correction_lands_only_on_the_words_it_corrects(self):
        corrected = storybook_corrections.apply_corrections(
            RECORDS, {0: Correction(0, "べつのことば", "イーヨーの誕生日")})
        self.assertEqual(corrected[0]["user_action"], "イオンの誕生日")

    def test_saved_beside_the_log_and_read_back(self):
        with tempfile.TemporaryDirectory() as directory:
            log = write_log(Path(directory), RECORDS)
            storybook_corrections.save(log, {
                0: Correction(0, "イオンの誕生日", "イーヨーの誕生日"),
                2: Correction(2, "たのしかった", "たのしかった"),  # back to what was heard
            })
            path = Path(directory) / "session_20261115T000000_abcdefgh_corrections.json"
            self.assertEqual(storybook_corrections.corrections_path(log), path)
            self.assertEqual(storybook_corrections.load(log),
                             {0: Correction(0, "イオンの誕生日", "イーヨーの誕生日")})
            records = read_log(log)
            self.assertEqual(records[0]["user_action"], "イーヨーの誕生日")
            self.assertEqual(read_log(log, corrected=False)[0]["user_action"], "イオンの誕生日")

    def test_unreadable_file_leaves_the_log_as_heard(self):
        with tempfile.TemporaryDirectory() as directory:
            log = write_log(Path(directory), RECORDS)
            storybook_corrections.corrections_path(log).write_text("{", encoding="utf-8")
            self.assertEqual(storybook_corrections.load(log), {})

    def test_the_book_from_the_log_tells_the_corrected_words(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(storybook, "compose_cover", return_value=None):
            log = write_log(Path(directory), RECORDS)
            storybook_corrections.save(log, {0: Correction(0, "イオンの誕生日", "イーヨーの誕生日"),
                                             2: Correction(2, "たのしかった", "またあそぼう")})
            book = build_from_log(log, "landscape")
            story = [item.entry.text for page in book.story_pages for item in page.placed]
            epilogue = [item.entry.text for page in book.epilogue_pages for item in page.placed]
            self.assertIn("「イーヨーの誕生日」と、あなたは いいました。", story)
            self.assertFalse(any("イオン" in text for text in story))
            self.assertIn("「またあそぼう」と、あなたは いいました。", epilogue)


class Recorder(FakeBackend):
    def store_page(self, page, slot, epilogue_pages=None):
        self.calls.append(("store", slot, [item.entry.text for item in page.placed], epilogue_pages))

    def replace_page(self, page, index, story_pages):
        self.calls.append(("replace", index, [item.entry.text for item in page.placed], story_pages))


def book(*said: str, epilogue: tuple[str, ...] = ()) -> StoryBuilder:
    builder = StoryBuilder(EEYORE, LAYOUTS["landscape"], full_width)
    builder.add(StoryTurn("session_open", "やあ。"))
    for text in said:
        builder.add(StoryTurn("participant", "うん。", user_text=text))
    builder.add(StoryTurn("story_goal", ""))
    for text in epilogue:
        builder.add(StoryTurn("participant", "うん。", user_text=text))
    return builder


class PushCorrectedBookTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(storybook, "compose_cover", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_the_pages_that_changed_go(self):
        old = book("ひとつめ", "ふたつめ", epilogue=("あと",))
        new = book("ひとつめ", "ふたつめ!", epilogue=("あと",))
        backend = Recorder()
        self.assertEqual(storybook_live.push_corrected_book(backend, new, old), 1)
        (kind, index, texts, count), = backend.calls
        self.assertEqual((kind, count), ("replace", len(new.story_pages)))
        self.assertIn("「ふたつめ!」と、あなたは いいました。", texts)

    def test_a_shorter_story_says_how_many_pages_are_left(self):
        long_words = "ながい" * 60
        old = book(long_words, long_words, long_words, long_words)
        new = book("みじかい", "みじかい", "みじかい", "みじかい")
        self.assertGreater(len(old.story_pages), len(new.story_pages))
        backend = Recorder()
        storybook_live.push_corrected_book(backend, new, old)
        self.assertTrue(backend.calls)
        self.assertTrue(all(call[3] == len(new.story_pages) for call in backend.calls))

    def test_without_the_old_book_every_page_goes(self):
        new = book("ひとつめ", epilogue=("あと",))
        backend = Recorder()
        storybook_live.push_corrected_book(backend, new)
        self.assertEqual([call[:2] for call in backend.calls],
                         [("replace", i) for i in range(len(new.story_pages))] + [("store", 0)])


class EinkReplacePageTest(unittest.TestCase):
    def test_story_page_goes_with_its_picture_for_the_viewer_to_keep(self):
        sent = []

        def send(event_id, manifest, tmp, files, host, app_dir):
            sent.append((event_id, manifest, [Path(f).name for f in files],
                         [Path(f).is_file() for f in files]))

        push = SimpleNamespace(BG=(255, 255, 255), EINK_HOST="tablet", _send_event=send)
        backend = storybook_live.EinkBackend(push=push, app_dir="/app")
        backend.layout = LAYOUTS["landscape"]
        with patch.object(storybook, "compose_cover", return_value=None):
            builder = book("いいよ")
        page = builder.story_pages[0]
        self.assertIsNotNone(page.illustration)
        backend.replace_page(page, 0, 3)
        (event_id, manifest, files, exists), = sent
        self.assertEqual(manifest["stages"], [])
        self.assertEqual((manifest["replace_page"], manifest["story_pages"]), (0, 3))
        self.assertEqual(manifest["stored_event"], event_id)
        self.assertEqual(files, ["picture.png"])
        self.assertEqual(exists, [True])
        box = LAYOUTS["landscape"].picture_box
        picture = manifest["stored_picture"]
        self.assertEqual(picture["image"], "picture.png")
        self.assertTrue(box[0] <= picture["x"] < box[0] + box[2])
        self.assertTrue(box[1] <= picture["y"] < box[1] + box[3])


def drain(live, timeout=10.0, until=lambda: True):
    deadline = time.monotonic() + timeout
    while (not live._queue.empty() or not until()) and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.1)


class LiveCorrectionTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(storybook, "compose_cover", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_finished_live_book_takes_up_corrections(self):
        with tempfile.TemporaryDirectory() as log_dir, \
                patch.dict("os.environ", {"POOH_LOG_DIR": log_dir}):
            backend = Recorder("landscape")
            live = storybook_live.LiveStorybook([backend])
            try:
                session = fake_session("k" * 32)
                session.log_path = Path(log_dir) / "session_live_kkkkkkkk.jsonl"
                live(session, fake_output("session_open"))
                live(session, fake_output("participant", user="イオンの誕生日"))
                live(session, fake_output("story_goal", ""))
                live(session, fake_output("participant", user="たのしかつた"))
                drain(live, until=lambda: live.saved)
                done = len(backend.calls)
                storybook_corrections.save(session.log_path, {
                    0: Correction(0, "イオンの誕生日", "イーヨーの誕生日"),
                    1: Correction(1, "たのしかつた", "たのしかった"),
                })
                drain(live, until=lambda: len(backend.calls) > done + 1)
                after = backend.calls[done:]
                self.assertTrue(any(call[0] == "replace" and
                                    "「イーヨーの誕生日」と、あなたは いいました。" in call[2]
                                    for call in after), after)
                self.assertTrue(any(call[0] == "store" and
                                    "「たのしかった」と、あなたは いいました。" in call[2]
                                    for call in after), after)
                # What is said afterwards goes on from the corrected book.
                live(session, fake_output("participant", user="またね"))
                drain(live)
                epilogue = [item.entry.text for page in live.builder.epilogue_pages
                            for item in page.placed]
                self.assertIn("「たのしかった」と、あなたは いいました。", epilogue)
                self.assertIn("「またね」と、あなたは いいました。", epilogue)
            finally:
                live.close()


class TabletStateTest(unittest.TestCase):
    def test_live_book_owns_the_tablet_until_it_closes(self):
        with tempfile.TemporaryDirectory() as log_dir, \
                patch.dict("os.environ", {"POOH_LOG_DIR": log_dir}):
            push = SimpleNamespace(read_orientation=lambda app_dir: "landscape")
            eink = storybook_live.EinkBackend(push=push)
            with patch.object(storybook_live.EinkBackend, "begin_book"), \
                    patch.object(storybook_live.EinkBackend, "new_page"), \
                    patch.object(storybook_live.EinkBackend, "write"):
                live = storybook_live.LiveStorybook([eink])
                session = fake_session("m" * 32)
                session.log_path = Path(log_dir) / "session_m.jsonl"
                live(session, fake_output("session_open"))
                drain(live)
                state = storybook_live.read_tablet_state()
                self.assertEqual(state["log"], str(session.log_path.resolve()))
                self.assertEqual((state["layout"], state["pid"]), ("landscape", __import__("os").getpid()))
                live.close()
            self.assertIsNone(storybook_live.read_tablet_state()["pid"])
            self.assertFalse(storybook_live.process_alive(None))


class EditorTest(AioHTTPTestCase):
    async def get_application(self):
        self.directory = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.directory.name)
        self.log = write_log(self.log_dir, RECORDS)
        self.env = patch.dict("os.environ", {"POOH_LOG_DIR": self.directory.name})
        self.env.start()
        self.cover = patch.object(storybook, "compose_cover", return_value=None)
        self.cover.start()
        return storybook_editor.create_app(self.log_dir, eink=False)

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.cover.stop()
        self.env.stop()
        self.directory.cleanup()

    async def test_lists_logs_and_corrects_an_utterance(self):
        response = await self.client.get("/corrections")
        self.assertEqual(response.status, 200)
        self.assertIn("聞き間違いの訂正", await response.text())
        logs = (await (await self.client.get("/corrections/api/logs")).json())["logs"]
        self.assertEqual([(log["name"], log["utterances"], log["corrected"]) for log in logs],
                         [(self.log.name, 3, 0)])
        url = f"/corrections/api/logs/{self.log.name}"
        detail = await (await self.client.get(url)).json()
        self.assertEqual(detail["utterances"][1]["said"], "どう ぞ")

        response = await self.client.put(f"{url}/utterances/1",
                                         json={"heard": RECORDS[2]["user_action"], "text": "どうぞ"})
        self.assertEqual(response.status, 200)
        detail = await response.json()
        self.assertEqual(detail["refreshed"], "pdf")
        self.assertEqual(detail["utterances"][1]["corrected"], "どうぞ")
        self.assertTrue((self.log_dir / "storybooks" / f"{self.log.stem}.pdf").is_file())
        self.assertEqual(read_log(self.log)[2]["user_action"], "「どうぞ」と言って、風船をプーに手渡す")

        # Back to what was heard drops the correction.
        await self.client.put(f"{url}/utterances/1",
                              json={"heard": RECORDS[2]["user_action"], "text": "どう ぞ"})
        self.assertEqual(storybook_corrections.load(self.log), {})

    async def test_refuses_a_correction_for_words_the_log_does_not_have(self):
        response = await self.client.put(f"/corrections/api/logs/{self.log.name}/utterances/0",
                                         json={"heard": "ちがう", "text": "x"})
        self.assertEqual(response.status, 409)
        response = await self.client.get("/corrections/api/logs/..%2Fsecret.jsonl")
        self.assertEqual(response.status, 404)

    async def test_pdf_of_the_corrected_book(self):
        response = await self.client.get(
            f"/corrections/api/logs/{self.log.name}/storybook.pdf?layout=portrait")
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.read()).startswith(b"%PDF"))


class EditorRefreshTest(unittest.TestCase):
    def test_leaves_the_tablet_to_a_running_live_book(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"POOH_LOG_DIR": directory}):
            log = write_log(Path(directory), RECORDS)
            storybook_live.write_tablet_state(log, "landscape", __import__("os").getpid())
            self.assertEqual(storybook_editor.refresh_book(log), "live")

    def test_sends_every_page_when_no_live_book_has_the_tablet(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"POOH_LOG_DIR": directory}), \
                patch.object(storybook, "compose_cover", return_value=None):
            log = write_log(Path(directory), RECORDS)
            storybook_live.write_tablet_state(log, "portrait", None)
            pushed = []
            with patch.object(storybook_live, "EinkBackend",
                              lambda: SimpleNamespace(layout=None)), \
                    patch.object(storybook_live, "push_corrected_book",
                                 lambda backend, builder: pushed.append((backend.layout, builder))):
                self.assertEqual(storybook_editor.refresh_book(log), "tablet")
            (layout, builder), = pushed
            self.assertEqual((layout.name, builder.layout.name), ("portrait", "portrait"))
            self.assertTrue((Path(directory) / "storybooks" / f"{log.stem}.pdf").is_file())


if __name__ == "__main__":
    unittest.main()
