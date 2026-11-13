//! The picture book's e-ink viewer: plush_memory's viewer (its library's
//! `viewer::run`), plus reading the finished book back.
//!
//! While a book is written, it behaves exactly like plush_memory_viewer.
//! Each page is kept as it stands when the PC marks it done (`page_end`);
//! once the PC marks the book done (`book_end` on its last page end), a tap
//! on the right third of the panel turns to the next page and one on the
//! left third to the previous one, going round from the last page to the
//! first and back. Each turn blanks the panel first and holds it blank for
//! `turn_clear_ms` (sent with `book_end`), as a new page does, so the page
//! turned from does not ghost through. The next book (`book_start`) forgets
//! the pages; any event at all ends reading, so a touch never turns a page
//! of a book being written.
//!
//! The book is done at the story's goal while the conversation may go on:
//! what is said after it (the epilogue) comes page by page (`stored_page`:
//! its place in the epilogue, 0, 1, …, and the page's text in `stored_text`,
//! laid out like text stages; its own `stages` are empty, so nothing is drawn),
//! written by the same pen as a live page but into a page of its own, kept
//! without being drawn or ending reading. They go after the story's last
//! page, before the cover (the last page at `book_end`); one sent again
//! replaces the page in its place.

use ab_glyph::FontRef;
use plush_memory_viewer::ink;
use plush_memory_viewer::qtfb::{InputEvent, INPUT_TOUCH_PRESS, INPUT_TOUCH_RELEASE};
use plush_memory_viewer::viewer::{self, Hooks, Screen};
use serde_json::Value;
use std::time::{Duration, Instant};

const NAME: &str = "storybook_viewer";
/// How long a page turn holds the panel blank, unless `book_end` says.
const DEFAULT_TURN_CLEAR_MS: u64 = 1500;
const WHITE: u8 = 0xff;
/// Longer than the viewer's poll interval, so every touch queued while a
/// turn held the panel blank is read (and dropped) within it.
const TAPS_DURING_TURN: Duration = Duration::from_millis(400);
/// Pages kept for reading back. Each is the whole panel, uncompressed
/// (about 7 MB); a session runs to a handful, so past this the oldest go.
const MAX_PAGES: usize = 40;

struct Page {
    /// The PC's number for it within the book (`page` on its page_end).
    number: Option<u64>,
    layout: &'static str,
    canvas: Vec<u8>,
}

#[derive(Default)]
struct Storybook {
    pages: Vec<Page>,
    /// The page shown while reading the finished book; None while writing.
    reading: Option<usize>,
    /// Where the current touch went down, in canvas coordinates.
    press: Option<(i32, i32)>,
    turn_clear: Duration,
    /// Touches before this (made while a turn was under way) are ignored.
    ignore_until: Option<Instant>,
    /// Where the epilogue goes (the cover's place at `book_end`), and how
    /// many of its pages are kept.
    epilogue_at: Option<usize>,
    epilogue_len: usize,
}

fn flag(manifest: &Value, key: &str) -> bool {
    manifest.get(key).and_then(Value::as_bool).unwrap_or(false)
}

impl Hooks for Storybook {
    fn on_event_start(&mut self, _screen: &mut Screen, manifest: &Value) {
        if manifest.get("stored_page").is_some() {
            return; // kept, not drawn: whatever page is up stays up
        }
        if flag(manifest, "book_start") {
            self.pages.clear();
            self.epilogue_at = None;
            self.epilogue_len = 0;
        }
        self.reading = None;
    }

    fn on_event_done(&mut self, screen: &mut Screen, manifest: &Value) {
        if let Some(slot) = manifest.get("stored_page").and_then(Value::as_u64) {
            self.store(screen, manifest, slot as usize);
            return;
        }
        let number = manifest.get("page").and_then(Value::as_u64);
        // The same page's mark sent twice (its reply was lost): keep it once.
        let again = number.is_some() && self.pages.last().map(|p| p.number) == Some(number);
        if flag(manifest, "page_end") && !flag(manifest, "idle") && !again {
            if self.pages.len() == MAX_PAGES {
                self.pages.remove(0);
                eprintln!("{NAME}: over {MAX_PAGES} pages; the first is no longer kept");
            }
            self.pages.push(Page { number, layout: screen.layout(), canvas: screen.canvas().to_vec() });
        }
        if flag(manifest, "book_end") && !self.pages.is_empty() {
            self.epilogue_at = Some(self.pages.len() - 1);
            self.epilogue_len = 0;
            self.reading = Some(self.pages.len() - 1);
            let ms = manifest.get("turn_clear_ms").and_then(Value::as_u64);
            self.turn_clear = Duration::from_millis(ms.unwrap_or(DEFAULT_TURN_CLEAR_MS));
            eprintln!("{NAME}: book done, {} page(s); tap the sides to turn", self.pages.len());
        }
    }

    fn on_input(&mut self, screen: &mut Screen, input: InputEvent, busy: bool) {
        let Some(current) = self.reading.filter(|_| !busy) else {
            return;
        };
        if self.ignore_until.is_some_and(|until| Instant::now() < until) {
            return;
        }
        let count = self.pages.len();
        match input.input_type {
            INPUT_TOUCH_PRESS => self.press = Some(screen.panel_to_canvas(input.x, input.y)),
            INPUT_TOUCH_RELEASE => {
                let Some((x, _)) = self.press.take() else { return };
                let (w, _) = screen.size();
                let target = if x > 2 * w as i32 / 3 {
                    (current + 1) % count
                } else if x < w as i32 / 3 {
                    (current + count - 1) % count
                } else {
                    return;
                };
                self.turn_to(screen, target);
            }
            _ => {}
        }
    }
}

impl Storybook {
    /// Keep an epilogue page in its place, or replace the one there.
    fn store(&mut self, screen: &Screen, manifest: &Value, slot: usize) {
        let Some(at) = self.epilogue_at else {
            eprintln!("{NAME}: epilogue page {} before the book was done; not kept", slot + 1);
            return;
        };
        let layout = manifest.get("stored_layout").and_then(Value::as_str).unwrap_or("");
        if layout != screen.layout() {
            eprintln!("{NAME}: epilogue page {} was {layout}, panel is {}; not kept", slot + 1, screen.layout());
            return;
        }
        let (w, h) = screen.size();
        let canvas = write_page(manifest, w, h);
        let page = Page { number: None, layout: screen.layout(), canvas };
        if slot < self.epilogue_len {
            self.pages[at + slot] = page;
        } else if slot == self.epilogue_len && self.pages.len() < MAX_PAGES {
            self.pages.insert(at + slot, page);
            self.epilogue_len += 1;
            // The page being read (the cover, after it) keeps its place.
            if let Some(reading) = self.reading.filter(|&r| r >= at + slot) {
                self.reading = Some(reading + 1);
            }
        } else {
            eprintln!("{NAME}: epilogue page {} not kept ({} kept, {} pages)", slot + 1,
                      self.epilogue_len, self.pages.len());
        }
    }

    fn turn_to(&mut self, screen: &mut Screen, index: usize) {
        let Some(page) = self.pages.get(index) else { return };
        if page.layout != screen.layout() {
            // The tablet has been turned since: the page no longer fits.
            eprintln!("{NAME}: page {} was {}, panel is {}; not shown", index + 1, page.layout, screen.layout());
            return;
        }
        // Blank, let the panel settle, then the page: drawing it straight over
        // the previous one left that one faintly showing through. Reading
        // means nothing else is being drawn, so waiting here holds up nothing.
        let blank = vec![WHITE; page.canvas.len()];
        screen.show(&blank);
        std::thread::sleep(self.turn_clear);
        screen.show(&page.canvas);
        self.reading = Some(index);
        // Taps made during the turn are only read after it: drop them.
        self.ignore_until = Some(Instant::now() + TAPS_DURING_TURN);
    }
}

/// A page `w` x `h` (RGB565) with the manifest's `stored_text` (text stages,
/// each with its own `y`) written on it by the viewer's own pen, as a live
/// page's text is, but all at once and off the panel.
fn write_page(manifest: &Value, w: usize, h: usize) -> Vec<u8> {
    let bg = manifest.get("bg").and_then(Value::as_array);
    let channel = |i: usize| bg.and_then(|c| c.get(i)).and_then(Value::as_u64).unwrap_or(255) as u16;
    let px565: u16 = ((channel(0) >> 3) << 11) | ((channel(1) >> 2) << 5) | (channel(2) >> 3);
    let mut canvas = px565.to_le_bytes().repeat(w * h);
    let font = FontRef::try_from_slice(ink::FONT_TTF).expect("bundled font");
    let stages = manifest.get("stored_text").and_then(Value::as_array).cloned().unwrap_or_default();
    for stage in &stages {
        let int = |key: &str| stage.get(key).and_then(Value::as_i64).unwrap_or(0) as i32;
        let text = stage.get("text").and_then(Value::as_str).unwrap_or("");
        let px = stage.get("px").and_then(Value::as_f64).unwrap_or(56.0) as f32;
        let center = stage.get("align").and_then(Value::as_str) == Some("center");
        let pen_r = stage.get("pen_r").and_then(Value::as_i64).map_or(ink::PEN_R, |r| r as i32);
        let (strokes, _) = ink::plan(&font, text, int("x"), int("y"), int("w"), px, center);
        let mut writer = ink::Writer::new(strokes, usize::MAX, pen_r);
        while !writer.done() {
            writer.step(&mut canvas, w, h);
        }
    }
    canvas
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("--preview") {
        viewer::preview(&args[2..]);
        return;
    }
    viewer::run(NAME, &mut Storybook::default());
}

#[cfg(test)]
mod tests {
    use super::write_page;
    use serde_json::json;

    #[test]
    fn a_stored_page_is_written_with_the_pen_off_the_panel() {
        let (w, h) = (400usize, 300usize);
        let blank = write_page(&json!({"stored_text": [], "bg": [255, 255, 255]}), w, h);
        assert_eq!(blank.len(), w * h * 2);
        assert!(blank.iter().all(|&b| b == 0xff));
        let page = write_page(&json!({"bg": [255, 255, 255], "stages": [], "stored_text": [
            {"x": 20, "y": 20, "w": 360, "px": 56.0, "text": "そのあと"},
            {"x": 20, "y": 120, "w": 360, "px": 56.0, "text": "またね。", "pen_r": 4},
        ]}), w, h);
        let inked = page.chunks_exact(2).filter(|px| px != &[0xff, 0xff]).count();
        assert!(inked > 100, "{inked}");
        // Nothing above the first line.
        assert!(page[..w * 10 * 2].iter().all(|&b| b == 0xff));
    }
}
