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
}

fn flag(manifest: &Value, key: &str) -> bool {
    manifest.get(key).and_then(Value::as_bool).unwrap_or(false)
}

impl Hooks for Storybook {
    fn on_event_start(&mut self, _screen: &mut Screen, manifest: &Value) {
        if flag(manifest, "book_start") {
            self.pages.clear();
        }
        self.reading = None;
    }

    fn on_event_done(&mut self, screen: &mut Screen, manifest: &Value) {
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

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("--preview") {
        viewer::preview(&args[2..]);
        return;
    }
    viewer::run(NAME, &mut Storybook::default());
}
