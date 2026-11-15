"""The epilogue's own picture and postcard: what was said after the story's
goal is drawn once the session ends, on the epilogue's first page, and
becomes one more card after the story's.

No image or text model, and no tablet, is used.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import postcard
import postcard_service
import storybook
import storybook_live
from postcard_service import PostcardMaker, postcard_path
from storybook import (EPILOGUE_BEAT, EPILOGUE_PICTURE_EVERY, EPILOGUE_PICTURES_MAX, LAYOUTS,
                       StoryBuilder, StoryTurn, epilogue_beat, epilogue_parts)
from storybook_illustrations import SessionIllustrations, epilogue_pictures
from test_storybook import (EEYORE, SAVED_LOG, FakeIllustrations, WithIllustrations, drain,
                            fake_output, fake_session)


def said(*texts: str) -> list[StoryTurn]:
    return [StoryTurn("participant", "うん。", user_text=text) for text in texts]


def numbered(count: int) -> list[str]:
    return [f"はなし{index + 1}" for index in range(count)]


class EpiloguePartsTest(unittest.TestCase):
    def test_a_new_part_every_so_many_things_said_up_to_the_most(self):
        goal = [StoryTurn("story_goal", "")]
        parts = epilogue_parts(goal + said(*numbered(2 * EPILOGUE_PICTURE_EVERY + 1)))
        self.assertEqual([len([t for t in part if t.user_text]) for part in parts],
                         [EPILOGUE_PICTURE_EVERY, EPILOGUE_PICTURE_EVERY, 1])
        self.assertEqual(parts[0][0].source, "story_goal")
        many = epilogue_parts(said(*numbered(EPILOGUE_PICTURE_EVERY * (EPILOGUE_PICTURES_MAX + 2))))
        self.assertEqual(len(many), EPILOGUE_PICTURES_MAX)  # the last runs on
        self.assertEqual([epilogue_beat(i) for i in range(3)], ["epilogue", "epilogue_2", "epilogue_3"])

    def test_pictures_only_of_parts_where_something_was_said(self):
        turns = [StoryTurn("session_open", "やあ。"), *said("まえ"), StoryTurn("story_goal", "")]
        self.assertEqual(epilogue_pictures(turns, 2, EEYORE), [])
        turns += said("あと")
        (beat_id, part), = epilogue_pictures(turns, 2, EEYORE)
        self.assertEqual((beat_id, part[0].source), (EPILOGUE_BEAT, "story_goal"))
        self.assertEqual(epilogue_pictures(turns, None, EEYORE), [])

    def test_every_book_has_the_parts_beats_from_the_shared_prompt(self):
        for scenario in ("eeyore_birthday", "footprints", "tea_party"):
            beats = storybook.load_story_config(scenario).beats
            self.assertTrue(all(epilogue_beat(i) in beats for i in range(EPILOGUE_PICTURES_MAX)))
            self.assertTrue(beats[EPILOGUE_BEAT].prompt)
            self.assertIsNone(beats[EPILOGUE_BEAT].illustration)


class BuilderEpiloguePartsTest(unittest.TestCase):
    def test_each_part_opens_a_page_for_its_picture(self):
        builder = StoryBuilder(EEYORE, LAYOUTS["landscape"], lambda text, px: float(px * len(text)))
        builder.add(StoryTurn("session_open", "やあ。"))
        builder.add(StoryTurn("story_goal", ""))
        for turn in said(*numbered(EPILOGUE_PICTURE_EVERY + 1)):
            builder.add(turn)
        beats = [page.beat_id for page in builder.epilogue_pages if page.beat_id]
        self.assertEqual(beats, ["epilogue", "epilogue_2"])
        second = next(page for page in builder.epilogue_pages if page.beat_id == "epilogue_2")
        self.assertEqual(second.placed[0].entry.text,
                         f"「はなし{EPILOGUE_PICTURE_EVERY + 1}」と、あなたは いいました。")


class SessionEpilogueTest(unittest.TestCase):
    def run_session(self, *after_goal: str, record=None) -> list[tuple[str, list[StoryTurn]]]:
        illustrations = SessionIllustrations(generate_image=lambda *a, **k: None)
        submitted = []
        session = fake_session("e" * 32)
        with patch.object(illustrations, "submit",
                          lambda config, beat_id, turns, **k: submitted.append((beat_id, list(turns)))):
            illustrations(session, fake_output("session_open"))
            illustrations(session, fake_output("participant", user="まえ"))
            illustrations(session, fake_output("story_goal", ""))
            for words in after_goal:
                illustrations(session, fake_output("participant", user=words))
                if record is not None:
                    record.append([beat for beat, _ in submitted if beat.startswith(EPILOGUE_BEAT)])
            illustrations(session, fake_output("session_close", "またね。", mode="exit"))
        illustrations._pool.shutdown(wait=True)
        return [(beat_id, turns) for beat_id, turns in submitted if beat_id.startswith(EPILOGUE_BEAT)]

    def test_drawn_from_what_was_said_after_the_goal_when_the_session_ends(self):
        (beat_id, turns), = self.run_session("たのしかった", "またあそぼう")
        self.assertEqual(turns[0].source, "story_goal")
        self.assertEqual([t.user_text for t in turns if t.user_text], ["たのしかった", "またあそぼう"])

    def test_not_drawn_when_nothing_was_said_after_the_goal(self):
        self.assertEqual(self.run_session(), [])

    def test_a_long_epilogue_s_parts_are_drawn_as_each_is_over(self):
        record = []
        words = numbered(EPILOGUE_PICTURE_EVERY + 2)
        submitted = self.run_session(*words, record=record)
        # Nothing while the first part goes on; it is drawn once the next opens.
        self.assertEqual(record[EPILOGUE_PICTURE_EVERY - 1], [])
        self.assertEqual(record[EPILOGUE_PICTURE_EVERY], [EPILOGUE_BEAT])
        self.assertEqual([beat for beat, _ in submitted], [EPILOGUE_BEAT, "epilogue_2"])
        first, second = (turns for _, turns in submitted)
        self.assertEqual([t.user_text for t in first if t.user_text], words[:EPILOGUE_PICTURE_EVERY])
        self.assertEqual([t.user_text for t in second if t.user_text], words[EPILOGUE_PICTURE_EVERY:])


class Recorder:
    def __init__(self):
        self.calls = []

    def orientation(self):
        return "landscape"

    def begin_book(self, layout):
        pass

    def new_page(self, event):
        pass

    def write(self, event, points_per_step):
        pass

    def add_picture(self, page):
        pass

    def end_book(self):
        self.calls.append(("end",))

    def store_page(self, page, slot, epilogue_pages=None):
        self.calls.append(("store", slot, page.illustration, epilogue_pages))


class LiveEpiloguePictureTest(WithIllustrations):
    def test_epilogue_page_keeps_room_for_its_picture_and_gets_it_at_the_end(self):
        session = fake_session("p" * 32)
        name = storybook.generated_illustration_name("eeyore_birthday", session.session_id, EPILOGUE_BEAT)

        class Drawing(FakeIllustrations):
            def wait(self, session_id, beat_id, cancelled=None):
                super().wait(session_id, beat_id, cancelled)
                if beat_id == EPILOGUE_BEAT:
                    from PIL import Image

                    path = storybook.ILLUSTRATION_ROOT / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    Image.new("RGB", (4, 4), "white").save(path)

        illustrations = Drawing()
        backend = Recorder()
        with patch.object(storybook, "compose_cover", return_value=None):
            live = storybook_live.LiveStorybook([backend], illustrations)
            try:
                live(session, fake_output("session_open"))
                live(session, fake_output("story_goal", ""))
                live(session, fake_output("participant", user="たのしかった"))
                drain(live)
                page = live.builder.epilogue_pages[0]
                self.assertTrue(page.awaits_illustration)
                self.assertEqual(page.text_columns, live.builder.layout.late_text_columns
                                 or live.builder.layout.text_columns)
                self.assertEqual(backend.calls[-1][:3], ("store", 0, None))
                live(session, fake_output("session_close", "またね。", mode="exit"))
                drain(live)
            finally:
                live.close()
        self.assertIn(EPILOGUE_BEAT, illustrations.waits)
        self.assertEqual(backend.calls[-1], ("store", 0, name, 1))


class LiveEpiloguePartPictureTest(WithIllustrations):
    def test_a_part_s_picture_goes_in_while_the_session_goes_on(self):
        session = fake_session("q" * 32)
        name = storybook.generated_illustration_name("eeyore_birthday", session.session_id, EPILOGUE_BEAT)
        illustrations = FakeIllustrations()
        backend = Recorder()
        with patch.object(storybook, "compose_cover", return_value=None), \
                patch.object(storybook_live, "PICTURE_POLL_SEC", 0.02):
            live = storybook_live.LiveStorybook([backend], illustrations)
            try:
                live(session, fake_output("session_open"))
                live(session, fake_output("story_goal", ""))
                for words in numbered(EPILOGUE_PICTURE_EVERY + 1):
                    live(session, fake_output("participant", user=words))
                drain(live)
                from PIL import Image

                path = storybook.ILLUSTRATION_ROOT / name
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (4, 4), "white").save(path)
                illustrations.ready.add(EPILOGUE_BEAT)
                deadline = time.monotonic() + 3
                while ("store", 0, name) not in [c[:3] for c in backend.calls] \
                        and time.monotonic() < deadline:
                    time.sleep(0.02)
            finally:
                live.close()
        self.assertIn(("store", 0, name), [c[:3] for c in backend.calls])


def eeyore_records_with_epilogue() -> list[dict]:
    records = [json.loads(line) for line in SAVED_LOG.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    records = [r for r in records if r.get("source") != "session_close"]
    session = records[0]
    base = {"scenario": session["scenario"], "session_id": session["session_id"]}
    return records + [
        {**base, "source": "story_goal"},
        {**base, "source": "participant", "user_action": "イーヨー、よろこぶかな",
         "bot_response": "きっと よろこぶよ。きみは なにを あげたい？", "interaction_mode": "narrative"},
        {**base, "source": "participant", "user_action": "わたしは おはなを あげたいな",
         "bot_response": "すてきだね。", "interaction_mode": "narrative"},
    ]


def pdf_pages(path: Path) -> int:
    import re

    return len(re.findall(rb"/Type\s*/Page(?!s)", path.read_bytes()))


class EpiloguePostcardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        env = patch.dict("os.environ", {"POOH_LOG_DIR": temporary.name})
        env.start()
        self.addCleanup(env.stop)
        for patcher in (patch.object(storybook, "compose_cover", return_value=None),
                        # No LM: the cards are picked without it.
                        patch.object(postcard, "lm_dialogue_plan",
                                     side_effect=RuntimeError("no LM in tests"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_log(self, records) -> Path:
        log = self.directory / "session_epilogue.jsonl"
        log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                       encoding="utf-8")
        return log

    def test_one_more_card_after_the_story_s(self):
        log = self.write_log(eeyore_records_with_epilogue())
        pdf = postcard_path(log)
        postcard_service.make_postcards(log, pdf, "l")
        story_cards = pdf_pages(pdf)
        self.assertEqual(postcard_service.make_epilogue_postcard(log, pdf, "l"), "extractive")
        self.assertEqual(pdf_pages(pdf), story_cards + 1)
        plan = json.loads(pdf.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(plan["source"], "extractive+epilogue:extractive")
        self.assertIn("わたしは おはなを あげたいな", plan["cards"][-1]["text"])
        # Made again, it replaces its card rather than adding another.
        postcard_service.make_epilogue_postcard(log, pdf, "l")
        self.assertEqual(pdf_pages(pdf), story_cards + 1)

    def test_none_when_nothing_was_said_after_the_goal(self):
        log = self.write_log(eeyore_records_with_epilogue()[:-2])
        pdf = postcard_path(log)
        self.assertIsNone(postcard_service.make_epilogue_postcard(log, pdf, "l"))
        self.assertFalse(pdf.exists())

    def test_epilogue_scene_borrows_the_story_s_last_picture_without_its_own(self):
        from storybook_render import build_from_log

        log = self.write_log(eeyore_records_with_epilogue())
        scene, = postcard.epilogue_scenes(build_from_log(log, "portrait"))
        self.assertEqual(scene.beat_id, EPILOGUE_BEAT)
        self.assertIsNotNone(scene.illustration)
        self.assertEqual(postcard.participant_words(scene.entries),
                         ["イーヨー、よろこぶかな", "わたしは おはなを あげたいな"])


class MakerEpilogueTest(unittest.TestCase):
    def test_the_epilogue_card_follows_when_a_session_past_its_goal_ends(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "session_x.jsonl"
            log.write_text("{}\n")
            session = SimpleNamespace(session_id="s1", log_path=log)
            made = []

            def make(log_path, pdf):
                made.append("story")
                return "lm"

            def make_epilogue(log_path, pdf):
                made.append("epilogue")
                return "lm"

            maker = PostcardMaker(None, make=make, make_epilogue=make_epilogue)
            try:
                maker(session, SimpleNamespace(source="story_goal", interaction_mode="narrative"))
                deadline = time.monotonic() + 5
                while maker.status("s1").status != "ready" and time.monotonic() < deadline:
                    time.sleep(0.01)
                maker(session, SimpleNamespace(source="session_close", interaction_mode="exit"))
                maker(session, SimpleNamespace(source="session_close", interaction_mode="exit"))
            finally:
                maker.close()
            self.assertEqual(made, ["story", "epilogue"])
            self.assertEqual(maker.status("s1").status, "ready")

    def test_a_session_that_ended_before_its_goal_gets_no_epilogue_card(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "session_x.jsonl"
            log.write_text("{}\n")
            session = SimpleNamespace(session_id="s1", log_path=log)
            made = []
            maker = PostcardMaker(None, make=lambda l, p: made.append("story") or "lm",
                                  make_epilogue=lambda l, p: made.append("epilogue") or "lm")
            try:
                maker(session, SimpleNamespace(source="session_close", interaction_mode="exit"))
            finally:
                maker.close()
            self.assertEqual(made, ["story"])


if __name__ == "__main__":
    unittest.main()
