"""Picture-book tests: narration, layout, live listener, e-ink backend.

No tablet, LLM or image API is used; text is measured as full-width
characters so the tests do not depend on plush_memory's font.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import storybook
import storybook_live
from scenarios import get_scenario
from storybook import (
    LAYOUTS,
    NewPage,
    StoryBuilder,
    StoryEntry,
    StoryTurn,
    WriteEntries,
    line_height,
    load_story_config,
    motion_sentence,
    narrate,
    paragraph_gap,
    wrap_text,
)
from storybook_render import read_log


def full_width(text: str, px: int) -> float:
    return float(px * len(text))


EEYORE = load_story_config("eeyore_birthday")
SAVED_LOG = (
    PROJECT_ROOT / "logs" / "saved_examples"
    / "eeyore_birthday_empty_jar_ribbon_2026-09-27.jsonl"
)


class WithIllustrations(unittest.TestCase):
    """Every beat of the Eeyore scenario has a (tiny) illustration file."""

    def setUp(self):
        from PIL import Image

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for beat in EEYORE.beats.values():
            path = root / beat.illustration
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (4, 4), "white").save(path)
        patcher = patch.object(storybook, "ILLUSTRATION_ROOT", root)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(storybook_live, "PAGE_READ_HOLD_SEC", 0)
        patcher.start()
        self.addCleanup(patcher.stop)


class NarrationTest(unittest.TestCase):
    def test_motion_sentence_chains_face_and_actions(self):
        sentence = motion_sentence(
            {"eyebrow": "sad", "arm": "left_hand_chin", "neck": "tilt"},
            EEYORE.motion_phrases,
        )
        self.assertEqual(sentence, "プーは かなしそうな かおで、あごに てを あてて、くびを かしげました。")

    def test_motion_sentence_covers_the_robot_side_vocabulary(self):
        sentence = motion_sentence(
            {"arm": "saved_r:right_hand_point", "neck": "disagree", "nose": "slight_nose"},
            EEYORE.motion_phrases,
        )
        self.assertEqual(
            sentence,
            "プーは みぎてで ゆびさして、くびを よこに ふって、はなを ひくひく させました。",
        )

    def test_motion_sentence_face_only_and_none(self):
        self.assertEqual(
            motion_sentence({"eyebrow": "happy"}, EEYORE.motion_phrases),
            "プーは うれしそうな かおを しました。",
        )
        self.assertEqual(motion_sentence({}, EEYORE.motion_phrases), "")
        self.assertEqual(motion_sentence({"arm": "unknown"}, EEYORE.motion_phrases), "")

    def test_participant_turn_quotes_both_sides_without_a_beat(self):
        beat, entries = narrate(
            StoryTurn("participant", "うん。", user_text="こんにちは"), EEYORE
        )
        self.assertIsNone(beat)
        self.assertEqual(
            [(e.style, e.speaker, e.text) for e in entries],
            [("speech", "participant", "「こんにちは」と、あなたは いいました。"),
             ("speech", "pooh", "「うん。」")],
        )

    def test_observed_actions_are_retold_without_speech(self):
        beat, entries = narrate(StoryTurn(
            "observed", "", observed_actions=("参加者がプーの左手をなでた",
                                              "参加者がはちみつの壺をプーの左手に手渡した")), EEYORE)
        self.assertIsNone(beat)
        self.assertEqual(
            [(e.style, e.text) for e in entries],
            [("narration", "あなたは プーの ひだりてを なでて、"
                           "はちみつの壺を プーの ひだりてに てわたしました。")],
        )

    def test_held_out_object_is_retold(self):
        self.assertEqual(storybook.observed_sentence("参加者がはちみつの壺をプーに差し出した"),
                         "あなたは はちみつの壺を プーに さしだしました。")
        self.assertEqual(storybook.observed_sentence("参加者がプーのおなかに触れた"),
                         "あなたは プーの おなかに さわりました。")

    def test_an_unknown_verb_is_kept_as_its_own_sentence(self):
        entries = storybook.participant_entries(
            ["参加者がプーの左手をなでた", "参加者がプーに手を振った"], "またね")
        self.assertEqual(
            [e.text for e in entries],
            ["あなたは プーの ひだりてを なでました。",
             "あなたは プーに手を振った。",
             "「またね」と、あなたは いいました。"],
        )

    def test_a_seen_handover_turn_quotes_only_what_was_said(self):
        _, entries = narrate(StoryTurn(
            "participant", "ありがとう。",
            user_text="「はちみつ どうぞ」と言って、はちみつの壺をプーの左手に手渡す"), EEYORE)
        self.assertEqual(entries[0].text, "「はちみつ どうぞ」と、あなたは いいました。")

    def test_a_seen_handover_and_its_words_make_one_sentence(self):
        builder = StoryBuilder(EEYORE, measure=full_width)
        builder.add(StoryTurn("session_open", "やあ。"))
        self.assertEqual(builder.add(StoryTurn(
            "observed", "", observed_actions=("参加者が風船をプーに差し出した",),
            with_speech=True)), [])
        builder.add(StoryTurn("participant", "ありがとう。",
                              user_text="「どうぞ」と言って、風船をプーに差し出す"))
        texts = [placed.entry.text for page in builder.pages for placed in page.placed]
        self.assertIn("あなたは 風船を プーに さしだしながら、「どうぞ」と いいました。", texts)
        self.assertNotIn("「どうぞ」と、あなたは いいました。", texts)

    def test_a_touch_is_not_joined_to_the_next_words(self):
        # A touch, or a handover whose words were dropped while Pooh was
        # busy, comes on its own; later words are about something else.
        builder = StoryBuilder(EEYORE, measure=full_width)
        builder.add(StoryTurn("session_open", "やあ。"))
        self.assertTrue(builder.add(StoryTurn(
            "observed", "", observed_actions=("参加者がプーのおなかに触れた",))))
        builder.add(StoryTurn("participant", "うん。", user_text="げんき？"))
        texts = [placed.entry.text for page in builder.pages for placed in page.placed]
        self.assertEqual(texts[-3:], ["あなたは プーの おなかに さわりました。",
                                      "「げんき？」と、あなたは いいました。", "「うん。」"])

    def test_seen_words_never_answered_are_retold_before_the_end(self):
        builder = StoryBuilder(EEYORE, measure=full_width)
        builder.add(StoryTurn("session_open", "やあ。"))
        builder.add(StoryTurn("observed", "", observed_actions=("参加者が風船をプーに差し出した",),
                              with_speech=True))
        builder.finish()
        texts = [placed.entry.text for page in builder.pages for placed in page.placed]
        self.assertEqual(texts[-2:], ["あなたは 風船を プーに さしだしました。", EEYORE.closing])

    def test_pooh_s_gesture_leads_into_the_line_it_goes_with(self):
        # Not a sentence of its own after a touch, where it would read as
        # Pooh's answer to it.
        builder = StoryBuilder(EEYORE, measure=full_width)
        builder.add(StoryTurn("session_open", "やあ。"))
        builder.add(StoryTurn("observed", "", observed_actions=("参加者がプーの左足に触れた",)))
        builder.add(StoryTurn("session_close", "またね。", ends_story=True,
                              motion={"eyebrow": "happy", "arm": "right_hand_bye"}))
        texts = [placed.entry.text for page in builder.pages for placed in page.placed]
        self.assertEqual(texts[-3:], ["あなたは プーの ひだりあしに さわりました。",
                                      "プーは うれしそうな かおで、てを ふって いいました。「またね。」",
                                      EEYORE.closing])
        _, entries = narrate(StoryTurn("participant", "うん。", motion={"eyebrow": "happy"}), EEYORE)
        self.assertEqual(entries[-1].text, "プーは うれしそうな かおで いいました。「うん。」")

    def test_touches_one_after_another_go_on_from_each_other(self):
        builder = StoryBuilder(EEYORE, measure=full_width)
        builder.add(StoryTurn("session_open", "やあ。"))
        for action in ("参加者がプーの左足に触れた", "参加者がプーの左足に触れた",
                       "参加者がプーのおなかをなでた"):
            builder.add(StoryTurn("observed", "", observed_actions=(action,)))
        builder.add(StoryTurn("participant", "くすぐったいよ。", user_text="えい"))
        builder.add(StoryTurn("observed", "", observed_actions=("参加者がプーの右手を握った",)))
        texts = [placed.entry.text for page in builder.pages for placed in page.placed]
        self.assertEqual(texts[-6:], [
            "あなたは プーの ひだりあしに さわりました。",
            "もう いちど、プーの ひだりあしに さわりました。",
            "それから、プーの おなかを なでました。",
            "「えい」と、あなたは いいました。",
            "「くすぐったいよ。」",
            "あなたは プーの みぎてを にぎりました。",
        ])

    def test_a_passage_run_on_overleaf_keeps_its_quote_whole(self):
        text = "プーは うれしそうな かおで いいました。「" + "あいうえお" * 12 + "」"
        lines = wrap_text(text, 10, 100, full_width)
        self.assertEqual(storybook.text_after(text, lines[:2]), "".join(lines[2:]))

    def test_observed_record_from_the_log(self):
        turn = storybook.turn_from_record({
            "source": "observed", "observed_actions": ["参加者がプーのおなかに触れた"]})
        self.assertEqual((turn.pooh_text, turn.user_text), ("", ""))
        self.assertEqual(turn.observed_actions, ("参加者がプーのおなかに触れた",))
        self.assertFalse(turn.with_speech)
        self.assertTrue(storybook.turn_from_record({
            "source": "observed", "observed_actions": ["参加者が風船をプーに差し出した"],
            "with_speech": True}).with_speech)

    def test_world_event_opens_its_beat_with_narration(self):
        beat, entries = narrate(
            StoryTurn("world_event", "あれ？", world_event_id="pooh_ate_honey"), EEYORE
        )
        self.assertEqual(beat, "pooh_ate_honey")
        self.assertEqual(entries[0].text, EEYORE.beats["pooh_ate_honey"].narration)

    def test_unlisted_world_event_stays_on_the_page(self):
        beat, entries = narrate(
            StoryTurn("world_event", "ふむ。", world_event_id="something_else"), EEYORE
        )
        self.assertIsNone(beat)
        self.assertEqual([e.style for e in entries], ["speech"])

    def test_opening_has_title(self):
        beat, entries = narrate(StoryTurn("session_open", "やあ。"), EEYORE)
        self.assertEqual(beat, "opening")
        self.assertEqual(entries[0].style, "title")
        self.assertEqual(entries[0].text, EEYORE.title)


class WrapTest(unittest.TestCase):
    def test_lines_fit_and_keep_all_text(self):
        text = "いまね、イーヨーにあってきたんだけど、かわいそうに、ひどいことになってるのさ。"
        lines = wrap_text(text, 10, 100, full_width)
        self.assertTrue(all(len(line) * 10 <= 100 for line in lines))
        self.assertEqual("".join(lines), text)

    def test_kinsoku_keeps_closing_marks_off_line_starts(self):
        # Ten characters fit; the eleventh is 「。」 and must not start a line.
        lines = wrap_text("あいうえおかきくけこ。さしす", 10, 100, full_width)
        self.assertFalse(any(line[0] in storybook.NO_LINE_START for line in lines))
        self.assertEqual("".join(lines), "あいうえおかきくけこ。さしす")

    def test_own_line_breaks_start_new_lines(self):
        # The viewer starts a line at each newline too; heights must agree.
        self.assertEqual(wrap_text("こんにちは\nさようなら", 10, 1000, full_width),
                         ["こんにちは", "さようなら"])

    @unittest.skipUnless(storybook.FONT_PATH.is_file(), "font not available")
    def test_measures_at_the_viewers_scale(self):
        # ab_glyph (eink-viewer script::measure) gives 386.74 px at px=56.
        self.assertAlmostEqual(storybook.default_measure("あいうえおかきくけこ", 56), 386.74, delta=1.0)

    def test_prefers_spaces_of_wakachigaki(self):
        lines = wrap_text("プーは はちみつの つぼを もちました", 10, 120, full_width)
        self.assertEqual(lines[0], "プーは はちみつの")


class BuilderTest(WithIllustrations):
    def builder(self):
        return StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)

    def test_opening_starts_an_illustrated_page(self):
        builder = self.builder()
        events = builder.add(StoryTurn("session_open", "やあ。"))
        self.assertIsInstance(events[0], NewPage)
        self.assertEqual(events[0].page.illustration, EEYORE.beats["opening"].illustration)
        self.assertEqual(events[0].page.text_columns, LAYOUTS["portrait"].text_columns)
        self.assertIsInstance(events[-1], WriteEntries)

    def test_entries_stack_the_way_the_viewer_continues_paragraphs(self):
        builder = self.builder()
        builder.add(StoryTurn("session_open", "やあ。"))
        placed = builder.pages[0].placed
        for before, after in zip(placed, placed[1:]):
            self.assertEqual(after.y, before.bottom + paragraph_gap(after.px))

    def test_beat_without_its_illustration_is_all_text(self):
        with patch.object(storybook, "ILLUSTRATION_ROOT", Path("/nonexistent")):
            builder = StoryBuilder(EEYORE, LAYOUTS["landscape"], full_width)
            builder.add(StoryTurn("session_open", "やあ。"))
        self.assertIsNone(builder.pages[0].illustration)
        self.assertEqual(builder.pages[0].text_columns, LAYOUTS["landscape"].plain_text_columns)

    def test_overflow_turns_to_a_plain_page(self):
        builder = self.builder()
        builder.add(StoryTurn("session_open", "やあ。"))
        events = []
        for _ in range(12):
            events += builder.add(StoryTurn("participant", "あ" * 40, user_text="い" * 20))
        new_pages = [event.page for event in events if isinstance(event, NewPage)]
        self.assertTrue(new_pages)
        self.assertIsNone(new_pages[0].illustration)
        self.assertEqual(new_pages[0].text_columns, LAYOUTS["portrait"].plain_text_columns)
        for page in builder.pages:
            x, top, w, h = page.text_box
            self.assertTrue(all(item.bottom <= top + h for item in page.placed))

    def test_beat_event_turns_the_page(self):
        builder = self.builder()
        builder.add(StoryTurn("session_open", "やあ。"))
        builder.add(StoryTurn("world_event", "あれ？", world_event_id="pooh_tastes_honey"))
        self.assertEqual([page.beat_id for page in builder.pages], ["opening", "pooh_tastes_honey"])

    def test_session_end_adds_closing_once(self):
        builder = self.builder()
        builder.add(StoryTurn("session_open", "やあ。"))
        events = builder.add(StoryTurn("session_close", "またね。", ends_story=True))
        self.assertEqual(events[-1].placed[-1].entry.style, "closing")
        self.assertEqual(builder.add(StoryTurn("participant", "おーい", user_text="x")), [])
        self.assertEqual(builder.finish(), [])

    def test_long_passage_continues_overleaf(self):
        builder = self.builder()
        builder.add(StoryTurn("session_open", ""))
        speech = "あ" * 26 * 3 + "い" * 26 * 12
        builder.add(StoryTurn("participant", speech))
        first, second = builder.pages[0].placed[-1], builder.pages[1].placed[0]
        self.assertEqual("".join(first.lines + second.lines), f"「{speech}」")
        self.assertIsNone(builder.pages[1].illustration)

    def test_continued_narration_keeps_its_spaces(self):
        builder = self.builder()
        builder._new_page(None)
        builder._place([StoryEntry("narration", "あ" * 1000)])  # fill most of the page
        page = builder.pages[-1]
        text = " ".join(["かきくけこさしす"] * 30)
        builder._place([StoryEntry("narration", text)])
        lines = [line for p in builder.pages[-2:] for item in p.placed
                 if item.entry.style == "narration" and "か" in item.lines[0] for line in item.lines]
        self.assertEqual(" ".join(lines), text)

    def test_little_room_moves_the_passage_whole(self):
        builder = self.builder()
        builder.add(StoryTurn("session_open", ""))
        x, top, w, h = builder.pages[0].text_box
        # Leave less than MIN_SPLIT_LINES lines on the page.
        filler_lines = (top + h - builder.pages[0].placed[-1].bottom) // line_height(storybook.BODY_PX) - 2
        builder._place([StoryEntry("narration", "ん" * 25 * filler_lines)])
        builder.add(StoryTurn("participant", "あ" * 200))
        self.assertEqual(builder.pages[-1].placed[0].entry.speaker, "pooh")
        self.assertTrue(builder.pages[-1].placed[0].lines[0].startswith("「"))

    def test_overlong_entry_runs_on_over_pages_without_losing_text(self):
        builder = self.builder()
        builder.add(StoryTurn("session_open", "やあ。"))
        builder.add(StoryTurn("participant", "あ" * 2000))
        parts = [item for page in builder.pages for item in page.placed
                 if item.entry.speaker == "pooh" and not item.lines[0].startswith("「や")]
        self.assertGreater(len({id(page) for page in builder.pages}), 2)
        self.assertEqual("".join(line for item in parts for line in item.lines), "「" + "あ" * 2000 + "」")
        for page in builder.pages:
            for item in page.placed:
                self.assertTrue(any(
                    x == item.x and top <= item.y and item.bottom <= top + h
                    for x, top, w, h in page.text_columns
                ))

    def test_landscape_plain_page_fills_left_then_right(self):
        builder = StoryBuilder(EEYORE, LAYOUTS["landscape"], full_width)
        builder.add(StoryTurn("session_open", ""))
        for _ in range(20):
            builder.add(StoryTurn("participant", "う" * 30, user_text="え" * 10))
        plain = next(page for page in builder.pages if page.illustration is None
                     and page is not builder.pages[-1])
        left, right = LAYOUTS["landscape"].plain_text_columns
        xs = [item.x for item in plain.placed]
        self.assertEqual(xs[0], left[0])
        self.assertIn(right[0], xs)
        self.assertEqual(xs, sorted(xs))

    def test_scene_without_picture_continues_on_the_right_page(self):
        with patch.object(storybook, "ILLUSTRATION_ROOT", Path("/nonexistent")):
            builder = StoryBuilder(EEYORE, LAYOUTS["landscape"], full_width)
            builder.add(StoryTurn("session_open", "やあ。"))
            builder.add(StoryTurn("world_event", "あれ？", world_event_id="pooh_tastes_honey"))
            # The right page is taken now, so the next scene turns the page.
            builder.add(StoryTurn("world_event", "ああ。", world_event_id="pooh_ate_honey"))
        left, right = LAYOUTS["landscape"].plain_text_columns
        first = builder.pages[0]
        tasting = next(item for item in first.placed
                       if item.entry.text == EEYORE.beats["pooh_tastes_honey"].narration)
        self.assertEqual((tasting.x, tasting.y), (right[0], right[1]))
        self.assertEqual(len(builder.pages), 2)
        self.assertEqual(builder.pages[1].beat_id, "pooh_ate_honey")

    def test_scene_with_picture_still_turns_the_page(self):
        builder = StoryBuilder(EEYORE, LAYOUTS["landscape"], full_width)
        with patch.object(storybook, "ILLUSTRATION_ROOT", Path("/nonexistent")):
            builder.add(StoryTurn("session_open", "やあ。"))
        builder.add(StoryTurn("world_event", "あれ？", world_event_id="pooh_tastes_honey"))
        self.assertEqual(len(builder.pages), 2)
        self.assertIsNotNone(builder.pages[1].illustration)

    def test_runs_break_at_a_column_change(self):
        builder = StoryBuilder(EEYORE, LAYOUTS["landscape"], full_width)
        builder.add(StoryTurn("session_open", ""))
        placed = []
        for _ in range(20):
            for event in builder.add(StoryTurn("participant", "う" * 30, user_text="え" * 10)):
                if isinstance(event, WriteEntries):
                    placed.extend(event.placed)
        for run in storybook_live._same_style_runs(placed):
            self.assertEqual(len({item.x for item in run}), 1)


class BundledFontTest(unittest.TestCase):
    def test_bundled_font_is_there(self):
        self.assertTrue(storybook.FONT_PATH.is_file())

    @unittest.skipUnless(storybook.VIEWER_FONT_PATH.is_file(), "plush_memory not available")
    def test_bundled_font_is_the_one_the_tablet_writes_with(self):
        # Lines are wrapped here for the tablet; a different font would
        # break them where the viewer does not.
        self.assertEqual(storybook.FONT_PATH.read_bytes(), storybook.VIEWER_FONT_PATH.read_bytes())


class RgbaCopyTest(unittest.TestCase):
    def test_palette_illustration_is_sent_with_its_alpha(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            source = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            source.paste((200, 150, 50, 255), (2, 2, 6, 6))
            path = Path(directory) / "p.png"
            source.quantize(16, method=Image.Quantize.FASTOCTREE).save(path)
            self.assertEqual(Image.open(path).mode, "P")
            with storybook_live.rgba_copy(path) as copy:
                sent = Image.open(copy)
                self.assertEqual(sent.mode, "RGBA")
                self.assertEqual(sent.split()[-1].getbbox(), (2, 2, 6, 6))


class FindIllustrationTest(unittest.TestCase):
    def test_paths_stay_inside_the_illustration_directory(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "storybook"
            root.mkdir()
            Image.new("RGB", (2, 2)).save(root / "a.png")
            Image.new("RGB", (2, 2)).save(Path(parent) / "outside.png")
            with patch.object(storybook, "ILLUSTRATION_ROOT", root):
                self.assertEqual(storybook.find_illustration("a.png"), (root / "a.png").resolve())
                self.assertIsNone(storybook.find_illustration("../outside.png"))
                self.assertIsNone(storybook.find_illustration("missing.png"))


class BookFromLogTest(unittest.TestCase):
    def test_saved_session_reads_as_a_book(self):
        records = read_log(SAVED_LOG)
        scenario = get_scenario("eeyore_birthday")
        book = storybook.build_book(
            records, scenario=scenario.key, opening_line=scenario.opening_line,
            measure=full_width,
        ).to_dict()
        beats = [page["beat_id"] for page in book["pages"] if page["beat_id"]]
        self.assertEqual(beats, ["opening", "pooh_tastes_honey", "pooh_ate_honey", "story_wrap_up"])
        texts = [entry["text"] for page in book["pages"] for entry in page["entries"]]
        self.assertIn("「ぷーのおばかさん」と、あなたは いいました。", texts)
        self.assertEqual(texts[-1], "おしまい")
        # The opening's motion comes from its fixed line, and leads into it.
        self.assertTrue(any(text.startswith(
            "プーは かなしそうな かおで、あごに てを あてて、くびを かしげて いいました。「いまね、")
            for text in texts))

    def test_logged_close_is_the_last_speech(self):
        scenario = get_scenario("eeyore_birthday")
        records = [
            {"scenario": "eeyore_birthday", "source": "participant",
             "user_action": "こんにちは", "bot_response": "やあ。", "interaction_mode": "narrative"},
            {"scenario": "eeyore_birthday", "source": "session_close",
             "bot_response": scenario.ending_line, "interaction_mode": "exit"},
        ]
        book = storybook.build_book(
            records, scenario=scenario.key, opening_line=scenario.opening_line,
            measure=full_width,
        )
        entries = [item.entry for page in book.pages for item in page.placed]
        self.assertEqual(entries[-2].text,
                         f"プーは うれしそうな かおで、てを ふって いいました。「{scenario.ending_line}」")
        self.assertEqual(entries[-1].style, "closing")


class FakeBackend:
    def __init__(self, orientation="portrait"):
        self._orientation = orientation
        self.calls = []

    def orientation(self):
        return self._orientation

    def begin_book(self, layout):
        self.calls.append(("begin", layout.name))

    def new_page(self, event):
        self.calls.append(("page", event.page.beat_id))

    def write(self, event, points_per_step):
        self.calls.append(("write", len(event.placed)))

    def end_book(self):
        self.calls.append(("end",))


def fake_session(session_id, scenario="eeyore_birthday"):
    return SimpleNamespace(session_id=session_id, scenario=SimpleNamespace(key=scenario))


def fake_output(source, text="やあ。", *, user="", event=None, mode="narrative"):
    return SimpleNamespace(
        source=source, bot_response=text, world_event_id=event, motion={},
        interaction_mode=mode, turn=SimpleNamespace(user_action=user) if user else None,
    )


def drain(live, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not live._queue.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)


class LiveStorybookTest(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend("landscape")
        self.live = storybook_live.LiveStorybook([self.backend])

    def tearDown(self):
        self.live.close()

    def test_follows_the_newest_session_only(self):
        first, second = fake_session("a" * 32), fake_session("b" * 32)
        self.live(first, fake_output("session_open"))
        self.live(second, fake_output("session_open"))
        self.live(first, fake_output("participant", user="ignored"))
        self.live(second, fake_output("participant", user="kept"))
        drain(self.live)
        self.assertEqual(self.live.builder.layout.name, "landscape")
        texts = [item.entry.text for page in self.live.builder.pages for item in page.placed]
        self.assertIn("「kept」と、あなたは いいました。", texts)
        self.assertNotIn("「ignored」と、あなたは いいました。", texts)
        # The abandoned first book was closed before the second began.
        self.assertEqual(self.backend.calls[:2], [("begin", "landscape"), ("page", "opening")])
        self.assertIn(("end",), self.backend.calls)

    def test_exit_finishes_the_book(self):
        session = fake_session("c" * 32)
        self.live(session, fake_output("session_open"))
        self.live(session, fake_output("participant", "またね。", user="ばいばい", mode="exit"))
        self.live(session, fake_output("participant", user="after"))
        drain(self.live)
        self.assertEqual(self.backend.calls[-1], ("end",))
        self.assertTrue(self.live.builder.finished)
        self.assertIsNone(self.live.session_id)

    def test_finished_book_is_saved_as_pdf_beside_the_log(self):
        with tempfile.TemporaryDirectory() as log_dir, \
                patch.dict("os.environ", {"POOH_LOG_DIR": log_dir}):
            session = fake_session("e" * 32)
            session.log_path = Path(log_dir) / "session_x_eeeeeeee.jsonl"
            self.live(session, fake_output("session_open"))
            self.live(session, fake_output("session_close", "またね。", mode="exit"))
            self.assertTrue(self.live.close())
            saved = Path(log_dir) / "storybooks" / "session_x_eeeeeeee.pdf"
            self.assertEqual(self.live.saved, [saved])
            self.assertTrue(saved.read_bytes().startswith(b"%PDF"))

    def test_book_taken_over_by_a_newer_session_is_not_saved(self):
        with tempfile.TemporaryDirectory() as log_dir, \
                patch.dict("os.environ", {"POOH_LOG_DIR": log_dir}):
            first, second = fake_session("h" * 32), fake_session("i" * 32)
            first.log_path = Path(log_dir) / "session_first.jsonl"
            second.log_path = Path(log_dir) / "session_second.jsonl"
            self.live(first, fake_output("session_open"))
            self.live(second, fake_output("session_open"))
            self.live(second, fake_output("session_close", "またね。", mode="exit"))
            self.assertTrue(self.live.close())
            self.assertEqual(self.live.saved, [Path(log_dir) / "storybooks" / "session_second.pdf"])

    def test_broken_book_does_not_stop_the_next_one(self):
        with patch.object(storybook, "load_story_config", side_effect=[ValueError("broken"), EEYORE]):
            self.live(fake_session("f" * 32), fake_output("session_open"))
            self.live(fake_session("g" * 32), fake_output("session_open"))
            drain(self.live)
        self.assertIsNotNone(self.live.builder)
        self.assertTrue(self.live._worker.is_alive())

    def test_backend_failure_does_not_stop_the_book(self):
        class Broken(FakeBackend):
            def new_page(self, event):
                raise OSError("tablet unreachable")

        broken = Broken()
        live = storybook_live.LiveStorybook([broken, self.backend])
        try:
            live(fake_session("d" * 32), fake_output("session_open"))
            drain(live)
        finally:
            live.close()
        self.assertIn(("page", "opening"), self.backend.calls)


class FakePush:
    def __init__(self):
        self.calls = []
        self.commands = []

    def read_orientation(self, **kwargs):
        return "portrait"

    def pending_events(self, **kwargs):
        return 0

    def ssh(self, command, *args, **kwargs):
        self.commands.append(command)

    def push_memory(self, path, cx, cy, **kwargs):
        self.calls.append(("memory", Path(path).name, kwargs))

    def push_text(self, paragraphs, **kwargs):
        self.calls.append(("text", paragraphs, kwargs))

    def push_page_end(self, event_id, **kwargs):
        self.calls.append(("page_end", event_id, kwargs))


class FailingIllustrationPush(FakePush):
    def push_memory(self, path, cx, cy, **kwargs):
        raise OSError("scp failed")


class EinkBackendTest(WithIllustrations):
    def test_failed_illustration_still_clears_and_starts_the_page(self):
        push = FailingIllustrationPush()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        push.calls.clear()  # the blank the book starts with
        events = builder.add(StoryTurn("session_open", "やあ。"))
        with self.assertRaises(OSError):
            backend.new_page(events[0])
        backend.write(events[-1], 12)
        _, _, kwargs = push.calls[0]
        self.assertTrue(kwargs["clear_first"])
        self.assertTrue(kwargs["page_start"])

    def test_event_ids_strictly_increase_and_centered_text_stays_centered(self):
        push = FakePush()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        push.calls.clear()  # the blank the book starts with
        for event in builder.add(StoryTurn("session_open", "やあ。")):
            if isinstance(event, NewPage):
                backend.new_page(event)
            else:
                backend.write(event, 12)
        backend.end_book()
        ids = [call[2]["event_id"] for call in push.calls if call[0] == "text"]
        ids.append(push.calls[-1][1])
        self.assertEqual(ids, sorted(set(ids), key=int))
        title = next(call for call in push.calls if call[0] == "text" and call[2]["align"] == "center")
        x, _, w, _ = builder.layout.text_columns[0]
        self.assertEqual(title[2]["x"] + title[2]["w"] / 2, x + w / 2)

    def test_page_without_illustration_clears_with_its_first_text(self):
        push = FakePush()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        push.calls.clear()  # the blank the book starts with
        for event in builder.add(StoryTurn("participant", "やあ。", user_text="こんにちは")):
            if isinstance(event, NewPage):
                backend.new_page(event)
            else:
                backend.write(event, 12)
        kinds = [call[0] for call in push.calls]
        self.assertEqual(kinds, ["text"])
        _, paragraphs, kwargs = push.calls[0]
        self.assertTrue(kwargs["clear_first"])
        self.assertTrue(kwargs["page_start"])
        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(kwargs["y"], builder.pages[0].placed[0].y)

    def test_page_turn_waits_until_the_tablet_has_drawn_the_page(self):
        class Busy(FakePush):
            backlog = [2, 1, 0]

            def pending_events(self, **kwargs):
                self.calls.append(("pending",))
                return self.backlog.pop(0) if self.backlog else 0

        push = Busy()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        push.calls.clear()  # the blank the book starts with
        with patch.object(storybook_live, "DRAIN_POLL_SEC", 0):
            backend.new_page(builder._new_page(None))
            backend.new_page(builder._new_page(None))
        kinds = [call[0] for call in push.calls]
        self.assertEqual(kinds, ["pending", "pending", "pending", "page_end"])

    def test_writing_hurries_while_the_tablet_is_behind(self):
        class Behind(FakePush):
            def pending_events(self, **kwargs):
                return 5

        push = Behind()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        push.calls.clear()  # the blank the book starts with
        for event in builder.add(StoryTurn("participant", "やあ。", user_text="こんにちは")):
            if isinstance(event, WriteEntries):
                backend.write(event, storybook_live.BASE_POINTS_PER_STEP)
        self.assertEqual(push.calls[0][2]["points_per_step"], storybook_live.RUSHED_POINTS_PER_STEP)
        self.assertEqual(push.calls[0][2]["clear_hold_ms"], storybook_live.PAGE_CLEAR_HOLD_MS)

    def test_book_is_marked_for_reading_back(self):
        push = FakePush()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        for turn in (StoryTurn("session_open", "やあ。"),
                     StoryTurn("world_event", "あれ？", world_event_id="pooh_tastes_honey"),
                     StoryTurn("session_close", "またね。", ends_story=True)):
            for event in builder.add(turn):
                if isinstance(event, NewPage):
                    backend.new_page(event)
                else:
                    backend.write(event, 12)
        backend.end_book()
        extras = [call[2].get("extra") for call in push.calls]
        self.assertEqual(extras[0], {"book_start": True})
        # Between them: nothing, except the page numbers of the page ends.
        self.assertTrue(all(extra is None or set(extra) == {"page"} for extra in extras[1:-1]))
        self.assertEqual(push.calls[-1][0], "page_end")
        self.assertEqual(extras[-1], {"page": 2, "book_end": True,
                                      "turn_clear_ms": storybook_live.PAGE_TURN_CLEAR_MS})
        self.assertTrue(all(call[2]["app_dir"] == storybook_live.STORYBOOK_APP_DIR
                            for call in push.calls))

    def test_entering_the_story_blanks_the_panel_first(self):
        push = FakePush()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        kind, paragraphs, kwargs = push.calls[0]
        self.assertEqual((kind, paragraphs), ("text", [""]))
        self.assertTrue(kwargs["clear_first"])
        self.assertEqual(kwargs["extra"], {"book_start": True})

    def test_book_start_survives_a_failed_first_push(self):
        class FlakyText(FakePush):
            failures = 1

            def push_text(self, paragraphs, **kwargs):
                if self.failures:
                    self.failures -= 1
                    raise OSError("scp failed")
                super().push_text(paragraphs, **kwargs)

        push = FlakyText()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)  # its blank fails
        events = builder.add(StoryTurn("participant", "やあ。", user_text="こんにちは"))
        backend.new_page(events[0])
        backend.write(events[1], 12)
        self.assertEqual(push.calls[0][2]["extra"], {"book_start": True})
        backend.write(events[1], 12)
        self.assertIsNone(push.calls[1][2]["extra"])

    def test_book_end_is_resent_until_it_lands(self):
        class FlakyEnd(FakePush):
            failures = 2

            def push_page_end(self, event_id, **kwargs):
                if self.failures:
                    self.failures -= 1
                    raise OSError("ssh failed")
                super().push_page_end(event_id, **kwargs)

        push = FlakyEnd()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        backend.new_page(builder._new_page(None))
        with patch.object(storybook_live, "END_MARK_RETRY_SEC", 0):
            backend.end_book()
        self.assertEqual(push.calls[-1][0], "page_end")
        self.assertTrue(push.calls[-1][2]["extra"]["book_end"])

    def test_a_resent_end_mark_carries_the_same_page_number(self):
        class ReplyLost(FakePush):
            lost = 1

            def push_page_end(self, event_id, **kwargs):
                super().push_page_end(event_id, **kwargs)  # it did land
                if self.lost:
                    self.lost -= 1
                    raise TimeoutError("ssh reply lost")

        push = ReplyLost()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        backend.new_page(builder._new_page(None))
        with patch.object(storybook_live, "END_MARK_RETRY_SEC", 0):
            backend.new_page(builder._new_page(None))
            backend.end_book()
        pages = [call[2]["extra"]["page"] for call in push.calls if call[0] == "page_end"]
        self.assertEqual(pages, [1, 1, 2])

    def test_book_end_that_never_lands_can_be_sent_again(self):
        class Down(FakePush):
            def push_page_end(self, event_id, **kwargs):
                raise OSError("ssh failed")

        push = Down()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        backend.new_page(builder._new_page(None))
        with patch.object(storybook_live, "END_MARK_RETRY_SEC", 0):
            with self.assertRaises(OSError):
                backend.end_book()
        # Still open: a later end_book tries again.
        self.assertTrue(backend._page_open)

    def test_new_page_starts_even_if_the_last_one_could_not_be_marked(self):
        class Down(FakePush):
            def push_page_end(self, event_id, **kwargs):
                raise OSError("ssh failed")

        push = Down()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        backend.new_page(builder._new_page(None))
        with patch.object(storybook_live, "END_MARK_RETRY_SEC", 0):
            backend.new_page(builder._new_page(None))
        self.assertTrue(backend._clear_pending)
        self.assertTrue(backend._page_open)

    def test_book_start_drops_incomplete_events_left_by_a_stopped_run(self):
        push = FakePush()
        backend = storybook_live.EinkBackend(push)
        backend.begin_book(LAYOUTS["portrait"])
        [command] = push.commands
        self.assertIn(f"{storybook_live.STORYBOOK_APP_DIR}/events", command)
        self.assertIn("READY", command)

    def test_new_page_ends_the_previous_one(self):
        push = FakePush()
        backend = storybook_live.EinkBackend(push)
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        backend.begin_book(builder.layout)
        page = builder._new_page(None)
        backend.new_page(page)
        backend.new_page(builder._new_page(None))
        backend.end_book()
        page_ends = [call for call in push.calls if call[0] == "page_end"]
        self.assertEqual(len(page_ends), 2)
        self.assertTrue(all(call[1] and call[1].isdigit() for call in page_ends))

    def test_runs_split_by_size(self):
        builder = StoryBuilder(EEYORE, LAYOUTS["portrait"], full_width)
        events = builder.add(StoryTurn("session_open", "やあ。"))
        placed = [item for event in events if isinstance(event, WriteEntries) for item in event.placed]
        runs = storybook_live._same_style_runs(placed)
        self.assertEqual(runs[0][0].entry.style, "title")
        self.assertEqual(len(runs[0]), 1)
        self.assertEqual(sum(len(run) for run in runs), len(placed))
        self.assertEqual(line_height(storybook.BODY_PX), 84)


if __name__ == "__main__":
    unittest.main()
