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
//! what is said after it (the epilogue) comes as whole pages
//! (`stored_page`: its place in the epilogue, 0, 1, …, and the PNG in the
//! event's own directory), kept without being drawn or ending reading. They
//! go after the story's last page, before the cover (the last page at
//! `book_end`); one sent again replaces the page in its place.

use plush_memory_viewer::qtfb::{InputEvent, INPUT_TOUCH_PRESS, INPUT_TOUCH_RELEASE};
use plush_memory_viewer::viewer::{self, Hooks, Screen};
use serde_json::Value;
use std::fs::File;
use std::io::BufReader;
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
        let path = manifest.get("stored_png").and_then(Value::as_str).unwrap_or("");
        let (w, h) = screen.size();
        let canvas = match read_page(path, w, h) {
            Ok(canvas) => canvas,
            Err(e) => {
                eprintln!("{NAME}: epilogue page {} unreadable: {e}", slot + 1);
                return;
            }
        };
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

/// A whole-panel PNG (RGB or RGBA, 8 bit) as an RGB565 canvas `w` x `h`.
fn read_page(path: &str, w: usize, h: usize) -> Result<Vec<u8>, String> {
    let file = File::open(path).map_err(|e| e.to_string())?;
    let mut decoder = png::Decoder::new(BufReader::new(file));
    decoder.set_transformations(png::Transformations::normalize_to_color8());
    let mut reader = decoder.read_info().map_err(|e| e.to_string())?;
    let mut buf = vec![0; reader.output_buffer_size()];
    let info = reader.next_frame(&mut buf).map_err(|e| e.to_string())?;
    if (info.width as usize, info.height as usize) != (w, h) {
        return Err(format!("{}x{}, panel is {w}x{h}", info.width, info.height));
    }
    let channels = match info.color_type {
        png::ColorType::Rgb => 3,
        png::ColorType::Rgba => 4,
        other => return Err(format!("{other:?}")),
    };
    let mut canvas = Vec::with_capacity(w * h * 2);
    for px in buf[..info.buffer_size()].chunks_exact(channels) {
        let (r, g, b) = (px[0] as u16, px[1] as u16, px[2] as u16);
        let px565: u16 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
        canvas.extend_from_slice(&px565.to_le_bytes());
    }
    Ok(canvas)
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
    use super::read_page;
    use std::fs::File;

    #[test]
    fn a_whole_panel_png_becomes_its_canvas() {
        let path = std::env::temp_dir().join("storybook_viewer_page_test.png");
        let (w, h) = (4usize, 2usize);
        let mut rgb = vec![255u8; w * h * 3];
        rgb[..3].copy_from_slice(&[255, 0, 0]);
        {
            let file = File::create(&path).unwrap();
            let mut encoder = png::Encoder::new(std::io::BufWriter::new(file), w as u32, h as u32);
            encoder.set_color(png::ColorType::Rgb);
            encoder.set_depth(png::BitDepth::Eight);
            encoder.write_header().unwrap().write_image_data(&rgb).unwrap();
        }
        let canvas = read_page(path.to_str().unwrap(), w, h).unwrap();
        assert_eq!(canvas.len(), w * h * 2);
        assert_eq!(&canvas[..2], &0xf800u16.to_le_bytes());
        assert_eq!(&canvas[2..4], &0xffffu16.to_le_bytes());
        assert!(read_page(path.to_str().unwrap(), h, w).is_err());
        let _ = std::fs::remove_file(path);
    }
}
