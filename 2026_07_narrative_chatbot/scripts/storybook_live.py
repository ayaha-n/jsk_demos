"""Write the picture book live while the session runs.

``LiveStorybook`` is an output listener for ``NarrativeSession``: each output
is laid out by ``storybook.StoryBuilder`` and the resulting page events are
drawn by a backend on a worker thread, so a slow tablet never holds up Pooh's
answer.  The book follows the newest session; outputs of any other session
(another browser tab) are ignored, since there is only one tablet.

Backends:

- ``EinkBackend`` pushes to the storybook viewer (``storybook_viewer/``, an
  AppLoad app built on plush_memory's e-ink viewer) on the reMarkable Paper
  Pro through ``plush_memory/scripts/eink_memory_push.py``: the illustration
  materializes first, then the text is written by hand.  Once the book is
  done, the viewer lets the reader turn back through its pages by tapping.
- ``PreviewBackend`` re-renders the current page as a PNG after each event,
  for working without the tablet.
"""

from __future__ import annotations

import contextlib
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Protocol

import storybook
from storybook import (
    LAYOUTS,
    NewPage,
    PageEvent,
    PageLayout,
    PlacedEntry,
    StoryBuilder,
    WriteEntries,
    find_illustration,
    turn_from_output,
)

# Writing pace (pen points per partial update): faster while turns pile up,
# so the book never falls far behind the conversation.
BASE_POINTS_PER_STEP = 12
HURRIED_POINTS_PER_STEP = 24
RUSHED_POINTS_PER_STEP = 48
# Extra box width handed to the viewer around lines wrapped here.
WRAP_SLACK_PX = 60
# Turning the page, after plush_memory's eink_hook: wait until the tablet has
# drawn everything sent for the page (polling like its _wait_drained), leave
# the finished page to be read a moment, then blank it and hold longer than
# eink_memory_push.CLEAR_HOLD_MS before writing, since a page of handwriting
# ghosted through a 1.5 s hold.
DRAIN_POLL_SEC = 1.0
DRAIN_TIMEOUT_SEC = 120.0
PAGE_READ_HOLD_SEC = 3.0
PAGE_CLEAR_HOLD_MS = 3000
# Reading the finished book back on the tablet: how long a page turn holds
# the panel blank before drawing the page, so the previous one does not
# ghost through. Sent with the book's end; the viewer needs no rebuild.
PAGE_TURN_CLEAR_MS = 1000
# Sending a page's end mark, which reading back depends on.
END_MARK_ATTEMPTS = 3
END_MARK_RETRY_SEC = 2.0
# Where the storybook viewer is installed on the tablet (see its README).
STORYBOOK_APP_DIR = "/home/root/xovi/exthome/appload/storybook_viewer"


class Backend(Protocol):
    def orientation(self) -> str: ...
    def begin_book(self, layout: PageLayout) -> None: ...
    def new_page(self, event: NewPage) -> None: ...
    def write(self, event: WriteEntries, points_per_step: int) -> None: ...
    def end_book(self) -> None: ...


def import_eink_memory_push() -> Any:
    scripts = storybook.PLUSH_MEMORY_DIR / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import eink_memory_push

    return eink_memory_push


class EinkBackend:
    """The storybook viewer on the tablet, over ssh (EINK_HOST).

    Besides plush_memory's event keys, it marks the first event of a book
    (``book_start``) and the end of its last page (``book_end``), between
    which the viewer keeps each page to be read back."""

    def __init__(self, push: Any | None = None, app_dir: str = STORYBOOK_APP_DIR) -> None:
        self.push = push or import_eink_memory_push()
        self.app_dir = app_dir
        self.layout: PageLayout = LAYOUTS["portrait"]
        self._clear_pending = False
        self._page_open = False
        self._book_start = False
        self._page_number = 0
        self._last_id = 0

    def orientation(self) -> str:
        return self.push.read_orientation(app_dir=self.app_dir)

    def begin_book(self, layout: PageLayout) -> None:
        self.layout = layout
        self._book_start = True
        self._page_number = 0
        self._drop_incomplete_events()

    def _drop_incomplete_events(self) -> None:
        """Remove event dirs a stopped run left without READY: the viewer
        never draws them. Events are sent one at a time, so at a book's
        start none is still on its way."""
        try:
            self.push.ssh(
                f"cd {self.app_dir}/events 2>/dev/null || exit 0; "
                'for d in */; do [ -f "$d/READY" ] || rm -rf -- "$d"; done'
            )
        except Exception as exc:
            _report(self, exc)

    def new_page(self, event: NewPage) -> None:
        if self._page_open:
            self._wait_drained()
            time.sleep(PAGE_READ_HOLD_SEC)
        try:
            self._end_page()
        except Exception as exc:
            # Only that page is missing from the read-back; the new page
            # still starts on a blank panel.
            _report(self, exc)
            self._page_open = False
        # Until something lands on the new page, the next push blanks the
        # panel and starts the page; a failed illustration push leaves this
        # set, so the text does not end up over the previous page.
        self._clear_pending = True
        self._page_open = True
        path = find_illustration(event.page.illustration) if event.page.illustration else None
        if path is not None:
            x, y, w, h = self.layout.picture_box
            with rgba_copy(path) as image:
                self.push.push_memory(
                    str(image), x + w // 2, y + h // 2, target_w=w, max_h=h,
                    clear_first=True, page_start=True, layout=self.layout.name,
                    clear_hold_ms=PAGE_CLEAR_HOLD_MS, event_id=self._next_id(),
                    app_dir=self.app_dir, extra=self._extra(),
                )
            self._clear_pending = False
            self._book_start = False

    def write(self, event: WriteEntries, points_per_step: int) -> None:
        # What the tablet has yet to draw is the real backlog: pushes return
        # long before the handwriting is done.
        points_per_step = max(points_per_step, pace_for_backlog(self._pending()))
        for group in _same_style_runs(event.placed):
            first = group[0]
            # The lines are already wrapped; the slack keeps the viewer's own
            # measurement from wrapping them again, split on both sides so a
            # centered line stays centered.
            self.push.push_text(
                ["\n".join(item.lines) for item in group],
                x=first.x - WRAP_SLACK_PX // 2, y=first.y,
                w=first.w + WRAP_SLACK_PX, px=first.px, align=first.align,
                clear_first=self._clear_pending, page_start=self._clear_pending,
                layout=self.layout.name, points_per_step=points_per_step,
                clear_hold_ms=PAGE_CLEAR_HOLD_MS, event_id=self._next_id(),
                app_dir=self.app_dir, extra=self._extra(),
            )
            self._clear_pending = False
            self._book_start = False

    def end_book(self) -> None:
        self._end_page(book_end=True)

    def _end_page(self, book_end: bool = False) -> None:
        """Mark the page done, retrying a few times: the viewer keeps a page
        for reading back only on this mark, and starts reading only on the
        book's last one."""
        if not self._page_open:
            return
        # The page's number lets the viewer keep it once even if a mark that
        # did land is sent again because its reply was lost.
        self._page_number += 1
        extra: dict[str, Any] = {"page": self._page_number}
        if book_end:
            extra.update(book_end=True, turn_clear_ms=PAGE_TURN_CLEAR_MS)
        for attempt in range(END_MARK_ATTEMPTS):
            try:
                self.push.push_page_end(self._next_id(), app_dir=self.app_dir, extra=extra)
                break
            except Exception:
                if attempt + 1 == END_MARK_ATTEMPTS:
                    raise
                time.sleep(END_MARK_RETRY_SEC)
        self._page_open = False

    def _extra(self) -> dict[str, bool] | None:
        """``book_start`` until one of the book's events has been sent: a
        failed first push must not leave the previous book's pages in."""
        return {"book_start": True} if self._book_start else None

    def _pending(self) -> int:
        try:
            return self.push.pending_events(app_dir=self.app_dir)
        except Exception:
            return 0

    def _wait_drained(self) -> None:
        deadline = time.monotonic() + DRAIN_TIMEOUT_SEC
        while self._pending() > 0 and time.monotonic() < deadline:
            time.sleep(DRAIN_POLL_SEC)

    def _next_id(self) -> str:
        """Millisecond ids like eink_memory_push's own, but strictly
        increasing, since the viewer draws events in name order."""
        self._last_id = max(int(time.time() * 1000), self._last_id + 1)
        return str(self._last_id)


@contextlib.contextmanager
def rgba_copy(path: Path) -> Iterator[Path]:
    """The illustration as an RGBA PNG: the illustrations are kept as 256-color
    (palette) PNGs to stay small, and eink_memory_push crops to the subject
    only from an alpha channel."""
    from PIL import Image

    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / path.name
        Image.open(path).convert("RGBA").save(target)
        yield target


def _same_style_runs(placed: list[PlacedEntry]) -> list[list[PlacedEntry]]:
    """Consecutive entries one text event can carry: the viewer continues
    each paragraph right below the last at the same size and alignment, so
    a run breaks wherever ``StoryBuilder`` placed the next entry otherwise
    (another size, or the next column)."""
    runs: list[list[PlacedEntry]] = []
    for item in placed:
        last = runs[-1][-1] if runs else None
        if last is not None and (last.px, last.align, last.x) == (item.px, item.align, item.x) \
                and item.y == last.bottom + storybook.paragraph_gap(item.px):
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


class PreviewBackend:
    """Saves the page being written as ``<dir>/<book>/page_NN.png``."""

    def __init__(self, out_dir: Path, orientation: str = "portrait") -> None:
        self.out_dir = out_dir
        self._orientation = orientation
        self.layout: PageLayout = LAYOUTS[orientation]
        self.book_dir = out_dir
        self._books = 0

    def orientation(self) -> str:
        return self._orientation

    def begin_book(self, layout: PageLayout) -> None:
        from datetime import datetime

        self.layout = layout
        self._books += 1
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        self.book_dir = self.out_dir / f"book_{stamp}_{self._books}"
        self.book_dir.mkdir(parents=True, exist_ok=True)

    def new_page(self, event: NewPage) -> None:
        self._save(event.page)

    def write(self, event: WriteEntries, points_per_step: int) -> None:
        self._save(event.page)

    def end_book(self) -> None:
        pass

    def _save(self, page: storybook.StoryPage) -> None:
        from storybook_render import render_page

        render_page(page, self.layout).save(self.book_dir / f"page_{page.index + 1:02d}.png")


_STOP = object()
# Waiting for the book on shutdown: one ssh push may take up to
# eink_memory_push.SSH_TIMEOUT_SEC, and the ending is a few pushes.
CLOSE_TIMEOUT_SEC = 180.0


class LiveStorybook:
    """``NarrativeSession`` output listener that writes the book live.

    The session's thread only picks out the outputs of the session the book
    follows and queues them; the worker owns the ``StoryBuilder``, asks the
    tablet which way it is turned when a book begins, lays out each turn and
    draws it.  So a slow or unreachable tablet never delays Pooh, and a page
    is never read while it is still being laid out.
    """

    def __init__(self, backends: list[Backend]) -> None:
        self.backends = backends
        self.session_id: str | None = None
        self.builder: StoryBuilder | None = None
        self.saved: list[Path] = []  # PDFs of finished books
        self._log_path: Path | None = None
        self._lock = threading.Lock()
        self._queue: queue.Queue[Any] = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="storybook")
        self._worker.start()

    def __call__(self, session: Any, output: Any) -> None:
        """Called on the session's thread: pick out and hand off."""
        turn = turn_from_output(output)
        with self._lock:
            if output.source == "session_open":
                self.session_id = session.session_id
                self._queue.put(("begin", (session.scenario.key, getattr(session, "log_path", None))))
            elif session.session_id != self.session_id:
                return
            self._queue.put(("turn", turn))
            if turn.ends_story:
                self.session_id = None

    def close(self, timeout: float = CLOSE_TIMEOUT_SEC) -> bool:
        """Finish writing what is queued, then stop.  False if it timed out."""
        self._queue.put(_STOP)
        if self._queue.qsize() > 1:
            print("[絵本] 書きかけのページを書き終えるまで待っています…", file=sys.stderr)
        self._worker.join(timeout)
        return not self._worker.is_alive()

    def _read_orientation(self) -> str:
        for backend in self.backends:
            try:
                orientation = backend.orientation()
            except Exception as exc:
                _report(backend, exc)
                continue
            return orientation if orientation in LAYOUTS else "portrait"
        return "portrait"

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                try:
                    self._end_book()
                except Exception as exc:
                    print(f"[絵本] 本を閉じられませんでした: {type(exc).__name__}: {exc}",
                          file=sys.stderr)
                return
            try:
                self._handle(item)
            except Exception as exc:
                # A broken book (e.g. an unreadable config) is dropped; the
                # worker stays up for the next session's book.
                print(f"[絵本] この本を中止します: {type(exc).__name__}: {exc}", file=sys.stderr)
                self.builder = None

    def _handle(self, item: Any) -> None:
        kind, value = item
        if kind == "begin":
            # A newer session takes the tablet over. The older one may well
            # go on in its own browser, so its book is closed on the tablet
            # but not saved: a PDF of it now would stop half way.
            self._end_book(save=False)
            self.builder = None
            scenario, self._log_path = value
            layout = LAYOUTS[self._read_orientation()]
            self.builder = StoryBuilder(storybook.load_story_config(scenario), layout)
            self._each_backend(lambda backend: backend.begin_book(layout))
            return
        if self.builder is None or self.builder.finished:
            return
        for event in self.builder.add(value):
            self._draw(event)
        if self.builder.finished:
            self._each_backend(lambda backend: backend.end_book())
            self._save()

    def _end_book(self, save: bool = True) -> None:
        """Close an unfinished book: on shutdown (saved, as far as the
        session got), or when a newer session takes the tablet over."""
        if self.builder is not None and not self.builder.finished:
            for event in self.builder.finish():
                self._draw(event)
            self._each_backend(lambda backend: backend.end_book())
            if save:
                self._save()

    def _save(self) -> None:
        """Keep the finished book as a PDF beside the session's log, laid
        out as the tablet showed it, to read again afterwards."""
        if self.builder is None or self._log_path is None:
            return
        from storybook_render import default_pdf_path, write_pdf

        try:
            path = write_pdf(self.builder, default_pdf_path(Path(self._log_path)))
        except Exception as exc:
            print(f"[絵本] PDFを保存できませんでした: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        self.saved.append(path)
        print(f"[絵本] 保存しました: {path}", file=sys.stderr)

    def _draw(self, event: PageEvent) -> None:
        # Turns still waiting mean the conversation is ahead of the pen.
        pace = pace_for_backlog(self._queue.qsize())
        if isinstance(event, NewPage):
            self._each_backend(lambda backend: backend.new_page(event))
        else:
            self._each_backend(lambda backend: backend.write(event, pace))

    def _each_backend(self, call: Any) -> None:
        for backend in self.backends:
            try:
                call(backend)
            except Exception as exc:
                # The conversation goes on without the book.
                _report(backend, exc)


def pace_for_backlog(backlog: int) -> int:
    if backlog > 3:
        return RUSHED_POINTS_PER_STEP
    if backlog > 0:
        return HURRIED_POINTS_PER_STEP
    return BASE_POINTS_PER_STEP


def _report(backend: Any, exc: Exception) -> None:
    print(f"[絵本] {type(backend).__name__}: {type(exc).__name__}: {exc}", file=sys.stderr)


def create_live_storybook(*, eink: bool, preview_dir: Path | None) -> LiveStorybook | None:
    backends: list[Backend] = []
    if eink:
        backends.append(EinkBackend())
    if preview_dir is not None:
        backends.append(PreviewBackend(preview_dir))
    return LiveStorybook(backends) if backends else None
