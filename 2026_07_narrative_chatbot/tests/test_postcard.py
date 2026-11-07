"""ポストカード（scripts/postcard.py）のLMを呼ばない回帰テスト。"""

import re
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import postcard  # noqa: E402
import postcard_items  # noqa: E402
from postcard import CardPlan, Scene, check_plan, expand_draft, extractive_plan  # noqa: E402
from storybook import StoryEntry  # noqa: E402

PICTURE = "footprints/opening.png"


def scenes():
    return [
        Scene("opening", PICTURE, [
            StoryEntry("narration", "ゆきの もりで、プーは あしあとを みつけました。"),
            StoryEntry("speech", "「なにをー？」と、あなたは いいました。", "participant"),
            StoryEntry("speech", "「名前は「ウズウズ」がいいな。」", "pooh"),
        ]),
        Scene("start_following", PICTURE, [
            StoryEntry("narration", "プーと あなたは、あしあとを おいかけはじめました。"),
            StoryEntry("speech", "「うずまきがたの、、かたつむり！」と、あなたは いいました。",
                       "participant"),
        ]),
        Scene("fourth_animal", PICTURE, [
            StoryEntry("narration", "あしあとが もっと ふえて、８つに なりました。"),
        ]),
    ]


def plan(text3="足あとは8つになりました。"):
    return [
        CardPlan(0, 0, 0, "プーは雪の森で足あとを見つけました。"),
        CardPlan(1, 1, 1, "あなたは「うずまきがたの、かたつむり！」と言いました。"),
        CardPlan(2, 2, 2, text3),
    ]


class PostcardTests(unittest.TestCase):
    def test_nested_quote_stays_one(self):
        quotes = postcard.top_level_quotes("「名前は「ウズウズ」がいいな。」と言った")
        self.assertEqual([inside for _, _, inside in quotes], ["名前は「ウズウズ」がいいな。"])

    def test_good_plan_passes(self):
        # Tidied punctuation of a speech-recognition quote is allowed.
        self.assertEqual(check_plan(plan(), scenes(), 3), [])

    def test_reworded_quote_is_rejected(self):
        bad = plan()
        bad[1] = CardPlan(1, 1, 1, "あなたは「うずまきのかたつむり」と言いました。")
        errors = check_plan(bad, scenes(), 3)
        self.assertTrue(any("会話にない言葉" in error for error in errors), errors)

    def test_partition_must_cover_scenes_in_order(self):
        bad = plan()
        bad[1] = CardPlan(2, 2, 2, bad[1].text)
        self.assertTrue(check_plan(bad, scenes(), 3))
        self.assertTrue(check_plan(plan()[:2], scenes(), 3))

    def test_some_participant_words_are_required(self):
        bad = plan()
        bad[1] = CardPlan(1, 1, 1, "ふたりは足あとを追いかけました。")
        errors = check_plan(bad, scenes(), 3)
        self.assertTrue(any("参加者" in error for error in errors), errors)

    def test_too_long_is_rejected(self):
        errors = check_plan(plan("足あと" * 80), scenes(), 3)
        self.assertTrue(any("長すぎ" in error for error in errors), errors)

    def test_tokens_expand_to_exact_quotes_of_the_card_only(self):
        quotes = postcard.quotes_of(scenes())
        token = next(q.token for q in quotes if "かたつむり" in q.text)
        text, errors = expand_draft(f"あなたは{token}と言いました。", 1, 1, quotes)
        self.assertEqual(text, "あなたは「うずまきがたの、かたつむり！」と言いました。")
        self.assertEqual(errors, [])
        self.assertEqual(postcard.tidy_quote("わわ，たくさん、、いる．"), "わわ、たくさん、いる。")
        _, errors = expand_draft(f"{token}", 0, 0, quotes)
        self.assertTrue(errors)

    def test_extractive_plan_keeps_order_and_fits(self):
        result = extractive_plan(scenes(), 3)
        self.assertEqual(check_plan(result, scenes(), 3), [])
        self.assertLess(result[1].text.index("おいかけ"), result[1].text.index("かたつむり"))

    def test_poem_takes_only_short_lines(self):
        poem = plan()
        poem[0] = CardPlan(0, 0, 0, "ゆきの もり\nみなれない あしあと")
        self.assertEqual(check_plan(poem, scenes(), 3, postcard.POEM), [])
        poem[0] = CardPlan(0, 0, 0, "ゆきの もりで プーは みなれない あしあとを みつけました。" * 2)
        self.assertTrue(check_plan(poem, scenes(), 3, postcard.POEM))
        # The same text is fine as prose.
        self.assertEqual(check_plan(poem, scenes(), 3, postcard.PROSE), [])

    def test_session_picture_comes_first(self):
        mixed = scenes()
        mixed[0].entries.append(StoryEntry("narration", "プーは なまえを かんがえました。"))
        mixed.insert(1, Scene("ask_appearance", "generated/abc.png",
                              [StoryEntry("narration", "みためを はなしあいました。")],
                              postcard.PICTURE_SESSION))
        good = [CardPlan(0, 1, 1, "プーは雪の森で足あとを見つけました。"),
                CardPlan(2, 2, 2, "あなたは「うずまきがたの、かたつむり！」と言いました。"),
                CardPlan(3, 3, 3, "足あとは8つになりました。")]
        self.assertEqual(check_plan(good, mixed, 3), [])
        bundled = [CardPlan(0, 1, 0, good[0].text)] + good[1:]
        errors = check_plan(bundled, mixed, 3)
        self.assertTrue(any("生成した絵" in error for error in errors), errors)
        for card in extractive_plan(mixed, 3):
            self.assertIn(card.picture_scene,
                          postcard.best_pictures(mixed, card.first_scene, card.last_scene))

    def test_picture_kind_of_the_book(self):
        config = replace(postcard.storybook.load_story_config("footprints"), session_id="s1")
        own = postcard.storybook.generated_illustration_name("footprints", "s1", "opening")
        self.assertEqual(postcard.picture_kind(own, config, "opening"), postcard.PICTURE_SESSION)
        self.assertEqual(postcard.picture_kind("generated/other.png", config, "opening"),
                         postcard.PICTURE_GENERATED)
        self.assertEqual(postcard.picture_kind("footprints/opening.png", config, "opening"),
                         postcard.PICTURE_BUNDLED)

    def test_trail_walks_down_the_strip_clear_of_the_text(self):
        prints = postcard.item_for_beat("footprints", "reveal_direct")
        self.assertEqual(len(prints.trail), 2)  # paw and oval, taking turns
        card = postcard.with_item(CardPlan(0, 0, None, "プー「あしあとが ふえてる。」\nあなた「ほんとだ」"),
                                  [Scene("reveal_direct", None, item=prints)])
        layout = postcard.flow_layout(card, None)
        self.assertEqual(len(layout.trail), prints.count)
        self.assertEqual({name for name, _, _ in layout.trail}, set(prints.trail))
        ax, _, aw, _ = postcard.FLOW_AREA
        strip = ax + aw - postcard.TRAIL_STRIP_MM
        for line in layout.lines:
            self.assertLessEqual(line.x_mm + line.width_mm, strip + 0.01)
        for _, (x, _, w, _), _ in layout.trail:
            self.assertGreaterEqual(x + w, strip)

    def test_a_picture_full_of_footprints_gets_no_trail(self):
        trail = postcard.item_for_beat("footprints", "second_animal")
        rows = [Scene("second_animal", PICTURE, item=trail),
                Scene("fourth_animal", PICTURE, item=None,
                      no_item=postcard_items.beat_hides_item("footprints", "fourth_animal"))]
        self.assertTrue(rows[1].no_item)
        # Not even the other scene's trail, though the card covers it.
        self.assertEqual(postcard.with_item(CardPlan(0, 1, 1, ""), rows).item_trail, [])
        self.assertTrue(postcard.with_item(CardPlan(0, 1, 0, ""), rows).item_trail)

    def test_birthday_cards_wear_a_ribbon(self):
        frame = postcard_items.load_frame("eeyore_birthday")
        self.assertEqual(frame.style, "ribbon")
        footprints = postcard_items.load_frame("footprints")
        self.assertEqual(footprints.style, "lines")
        self.assertNotEqual(footprints.color, postcard_items.Frame.color)  # the prints' blue
        card = postcard.with_item(CardPlan(0, 0, None, "プー「ふうせん。」"),
                                  [Scene("opening", None, frame=frame)])
        self.assertEqual(card.frame_style, "ribbon")
        if frame.bow:  # drawn
            bow = postcard._bow_box(card)
            self.assertIsNotNone(bow)
            # The text keeps clear of the bow.
            layout = postcard.flow_layout(card, None)
            for line in layout.lines:
                self.assertFalse(line.top_mm < bow[1] + bow[3] and line.x_mm < bow[0] + bow[2])
        with tempfile.TemporaryDirectory() as tmp:
            path = postcard.write_pdf([card] * 3, [Scene("opening", None, frame=frame)], "t",
                                      Path(tmp) / "c.pdf", postcard.DIALOGUE)
            self.assert_postcard_pages(path.read_bytes(), postcard.DIALOGUE.page)

    def test_closing_card_may_take_what_was_said_just_before(self):
        talk = lambda *lines: [StoryEntry("speech", f"「{t}」", "pooh") if who == "pooh" else
                               StoryEntry("speech", f"「{t}」と、あなたは いいました。", "participant")
                               for who, t in lines]
        rows = [Scene("a", PICTURE, talk(("pooh", "どうしよう？"), ("participant", "リボンにしよう"))),
                Scene("b", PICTURE, talk(("pooh", "どの色？"), ("participant", "あか"),
                                         ("pooh", "あかにしよう。"))),
                Scene("c", PICTURE, talk(("pooh", "よかった。きっと喜ぶよ。")))]
        utterances = postcard.utterances_of(rows)
        pick = lambda i: postcard.SentencePick(utterance=f"u{i}", first_sentence=0, last_sentence=0)
        # The closing card, covering scenes 1-2: Pooh -> you -> Pooh from
        # just before the closing line nobody answered.
        closing = postcard.DialogueDraft(first_scene=1, last_scene=2, picture_scene=2,
                                         lines=[pick(2), pick(3), pick(4)])
        card, errors = postcard.build_dialogue_card(closing, rows, utterances)
        self.assertEqual(errors, [])
        # Still Pooh first.
        wrong = closing.model_copy(update={"lines": [pick(3), pick(4), pick(5)]})
        self.assertTrue(postcard.build_dialogue_card(wrong, rows, utterances)[1])
        # Not for a scene that is not the last one.
        middle = postcard.DialogueDraft(first_scene=0, last_scene=1, picture_scene=1,
                                        lines=[pick(0), pick(1)])
        self.assertTrue(postcard.build_dialogue_card(middle, rows, utterances)[1])
        # Nor from a scene on another card.
        alone = closing.model_copy(update={"first_scene": 2, "lines": [pick(2), pick(3)]})
        self.assertTrue(postcard.build_dialogue_card(alone, rows, utterances)[1])

    def test_pooh_alone_only_where_nobody_answered_him(self):
        alone = [Scene("a", PICTURE, [StoryEntry("speech", "「はちみつ、ひとくちだけ。」", "pooh")])]
        utterances = postcard.utterances_of(alone)
        draft = postcard.DialogueDraft(first_scene=0, last_scene=0, picture_scene=0, lines=[
            postcard.SentencePick(utterance="u0", first_sentence=0, last_sentence=0)])
        self.assertEqual(postcard.build_dialogue_card(draft, alone, utterances)[1], [])
        self.assertEqual(postcard.extractive_dialogue_plan(alone, 1)[0].text,
                         "プー「はちみつ、ひとくちだけ。」")

    def test_lines_break_between_phrases(self):
        self.assertEqual(postcard.phrases("ところで、ウズウズの見た目はどんな感じだと思う？"),
                         ["ところで、", "ウズウズの", "見た目はどんな", "感じだと", "思う？"])
        text = "あれ、このあしあとはぼくのあし、こっちのあしあとはきみのあしとおなじおおきさだね。"
        measure = postcard._pdf_measure
        for width_mm in (30, 40, 50):
            lines = postcard.wrap_phrases(text, 9.0, width_mm * postcard.MM, measure)
            self.assertEqual("".join(lines), text)
            for line in lines:
                self.assertLessEqual(measure(line, 9.0), width_mm * postcard.MM)
                # Never 「…おな｜じ…」: every line ends where a word does.
                self.assertFalse(line.endswith(("おな", "あしあ")), lines)

    def test_chat_rows_drop_names_and_brackets(self):
        rows = postcard.chat_rows("プー「名前は「ウズウズ」がいいな。」\nあなた「いいね」")
        self.assertEqual(rows, [("pooh", "名前は「ウズウズ」がいいな。"), ("participant", "いいね")])

    def test_pdf_is_postcard_size(self):
        for style in postcard.STYLES.values():
            with self.subTest(style=style.name), tempfile.TemporaryDirectory() as tmp:
                path = postcard.write_pdf(plan(), scenes(), "あしあとの しょうたい",
                                          Path(tmp) / "cards.pdf", style)
                self.assert_postcard_pages(path.read_bytes(), style.page)
        self.assertEqual(postcard.DIALOGUE.page, (148.0, 100.0))

    def assert_postcard_pages(self, data, page=(100.0, 148.0)):
        boxes = re.findall(rb"/MediaBox \[\s*0 0 ([\d.]+) ([\d.]+)\s*\]", data)
        self.assertEqual(len(boxes), 3)
        for width, height in boxes:
            self.assertAlmostEqual(float(width) / postcard.MM, page[0], places=1)
            self.assertAlmostEqual(float(height) / postcard.MM, page[1], places=1)

    def test_dialogue_quotes_only_what_was_said_on_that_card(self):
        quotes = postcard.utterances_of(scenes())
        said = next(u for u in quotes if "かたつむり" in u.sentences[0])
        card = CardPlan(1, 1, 1, postcard.dialogue_line(said, 0, 0))
        plan = [CardPlan(0, 0, 0, "あなた「なにをー？」"), card,
                CardPlan(2, 2, 2, "")]
        for style in (postcard.DIALOGUE, postcard.DIALOGUE_PORTRAIT):
            self.assertEqual(postcard.check_dialogue_plan(plan, scenes(), 3, style), [])
        # Said in the conversation, but not in this card's scenes.
        moved = [plan[0], CardPlan(1, 1, 1, "あなた「なにをー？」"), plan[2]]
        self.assertTrue(postcard.check_dialogue_plan(moved, scenes(), 3))

    def test_dialogue_draft_picks_lines_by_id_or_text(self):
        # In scenes(): u0 あなた「なにをー？」, u1 プー「名前は…」 (scene 0),
        # u2 あなた「うずまき…」 (scene 1).
        utterances = postcard.utterances_of(scenes())
        pick = postcard.SentencePick
        draft = postcard.DialogueDraft(
            first_scene=0, last_scene=1, picture_scene=1,
            lines=[pick(utterance="名前は「ウズウズ」がいいな。", first_sentence=0, last_sentence=0),
                   pick(utterance="u2", first_sentence=0, last_sentence=0)])
        card, errors = postcard.build_dialogue_card(draft, scenes(), utterances)
        self.assertEqual(card.text, "プー「名前は「ウズウズ」がいいな。」\nあなた「うずまきがたの、かたつむり！」")
        # Found by its text, but it runs across two scenes: not beside one picture.
        self.assertTrue(any("絵と合う" in e for e in errors), errors)

    def test_stock_picture_takes_the_start_of_its_scene(self):
        talk = [StoryEntry("speech", f"「ぼくの {n}。」", "pooh") if n % 2 == 0 else
                StoryEntry("speech", f"「あなたの {n}」と、あなたは いいました。", "participant")
                for n in range(8)]
        stock = [Scene("a", PICTURE, talk)]
        session = [Scene("a", PICTURE, talk, postcard.PICTURE_SESSION)]
        utterances = postcard.utterances_of(stock)
        pick = lambda i: postcard.SentencePick(utterance=f"u{i}", first_sentence=0, last_sentence=0)
        early = postcard.DialogueDraft(first_scene=0, last_scene=0, picture_scene=0,
                                       lines=[pick(2), pick(3)])
        late = early.model_copy(update={"lines": [pick(6), pick(7)]})
        self.assertEqual(postcard.build_dialogue_card(early, stock, utterances)[1], [])
        self.assertTrue(postcard.build_dialogue_card(late, stock, utterances)[1])
        # A picture drawn for this session shows the whole scene.
        self.assertEqual(postcard.build_dialogue_card(late, session, utterances)[1], [])
        card = postcard.extractive_dialogue_plan(stock, 1)[0]
        self.assertTrue(card.text.startswith("プー「ぼくの"), card.text)
        self.assertNotIn("6", card.text)

    def test_extractive_dialogue_fits_both_orientations(self):
        talk = [Scene(beat, PICTURE, [
            StoryEntry("speech", f"「{beat}は どうかな？」", "pooh"),
            StoryEntry("speech", f"「{beat}がいいよ」と、あなたは いいました。", "participant"),
            StoryEntry("speech", "「そうしよう。" + "ほんとうに いい かんがえだね。" * 4 + "」", "pooh"),
        ]) for beat in ("a", "b", "c")]
        for style in (postcard.DIALOGUE, postcard.DIALOGUE_PORTRAIT):
            plan = postcard.extractive_dialogue_plan(talk, 3, style)
            self.assertEqual(postcard.check_dialogue_plan(plan, talk, 3, style), [])
            for card in plan:
                self.assertEqual([s for s, _ in postcard.chat_rows(card.text)][:2],
                                 ["pooh", "participant"])
            with tempfile.TemporaryDirectory() as tmp:
                path = postcard.write_pdf(plan, talk, "t", Path(tmp) / "c.pdf", style)
                self.assert_postcard_pages(path.read_bytes(), style.page)

    def test_item_comes_from_the_picture_beat(self):
        # footprints: a trail of paw prints cut from the reference picture.
        item = postcard.item_for_beat("footprints", "start_following")
        self.assertEqual(item.trail, ("footprints/postcard_items/paw_print.png",))
        # eeyore_birthday: one drawn item in a corner.
        item = postcard.item_for_beat("eeyore_birthday", "story_wrap_up")
        if item is not None:
            self.assertEqual(item.picture, "eeyore_birthday/postcard_items/balloon.png")
            self.assertIn(item.anchor, postcard_items.ANCHORS)
        a = postcard_items.Item("a", "a.png", "bottom_left", 12.0)
        b = postcard_items.Item("b", "b.png", "top_right", 16.0)
        listed = [Scene("x", None), Scene("y", PICTURE, item=a), Scene("z", PICTURE, item=b)]
        card = postcard.with_item(CardPlan(0, 2, 1, ""), listed)
        self.assertEqual((card.item, card.item_anchor, card.item_size_mm), ("a.png", "bottom_left", 12.0))
        self.assertEqual(postcard.with_item(CardPlan(0, 0, None, ""), listed).item, "")

    def test_every_scenario_file_names_existing_items(self):
        for scenario in postcard_items.scenarios():
            config = postcard_items.load_scenario(scenario)
            with self.subTest(scenario=scenario):
                self.assertIsNotNone(config)
                for beat, name in config["beats"].items():
                    if name is not None:  # null: no item for this beat's picture
                        self.assertIn(name, config["items"], beat)
                for name, spec in config["items"].items():
                    for part in spec.get("trail", []):
                        self.assertIn(part, config["items"], name)

    def test_pooh_you_pooh_is_one_exchange(self):
        rows = [Scene("a", PICTURE, [
            StoryEntry("speech", "「どんな見た目だと思う？」", "pooh"),
            StoryEntry("speech", "「まるいかな」と、あなたは いいました。", "participant"),
            StoryEntry("speech", "「まるいのか、いいね。」", "pooh"),
            StoryEntry("speech", "「うん」と、あなたは いいました。", "participant"),
        ])]
        utterances = postcard.utterances_of(rows)
        pick = lambda i: postcard.SentencePick(utterance=f"u{i}", first_sentence=0, last_sentence=0)
        draft = postcard.DialogueDraft(first_scene=0, last_scene=0, picture_scene=0,
                                       lines=[pick(0), pick(1), pick(2)])
        card, errors = postcard.build_dialogue_card(draft, rows, utterances)
        self.assertEqual(errors, [])
        self.assertEqual([s for s, _ in postcard.chat_rows(card.text)],
                         ["pooh", "participant", "pooh"])
        _, errors = postcard.build_dialogue_card(
            draft.model_copy(update={"lines": [pick(0), pick(2)]}), rows, utterances)
        self.assertTrue(errors)  # not one after the other
        for lines in ([pick(0), pick(1), pick(2), pick(3)],  # too many
                      [pick(1), pick(2)],  # starts with the participant
                      [pick(0)]):  # Pooh alone
            _, errors = postcard.build_dialogue_card(
                draft.model_copy(update={"lines": lines}), rows, utterances)
            self.assertTrue(errors, lines)
        extract = postcard.extractive_dialogue_plan(rows, 1)[0]
        self.assertEqual(postcard.chat_rows(extract.text)[0], ("pooh", "どんな見た目だと思う？"))

if __name__ == "__main__":
    unittest.main()
