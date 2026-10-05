# Narrative Interaction with Pooh Using DSPy

This research prototype uses DSPy to interpret a participant's open-ended speech and physical actions in relation to a shared tea-party scene.

It is not intended to be a general-purpose character chatbot or a reproduction of the original stories. Its purpose is to support both:

- **Narrative coherence:** consistency with the current scene, previous events, characters, props, and goals.
- **Participant agency:** preserving the participant's actions and leaving subsequent actions open to their own choice.

See [`CODEX_INSTRUCTIONS_POOH_DSPY.md`](CODEX_INSTRUCTIONS_POOH_DSPY.md) for the full research and implementation requirements.

## Requirements

- Linux
- Python 3.12
- DSPy 3.2.1
- An OpenAI API key
- Internet access for:
  - Initial dependency installation
  - DSPy compilation and model inference

Use a project-local virtual environment so that the system Python installation
does not need to be replaced or upgraded. The `.venv` directory is excluded
from Git.

## Setup

### Activating an existing virtual environment

Move to the repository directory and activate the environment:

```bash
cd /path/to/jsk_demos/2026_07_narrative_chatbot
source .venv/bin/activate
```

Verify the installed versions:

```bash
python --version
python -c "import importlib.metadata as m; print(m.version('dspy'))"
```

The expected versions are:

```text
Python 3.12.x
3.2.1
```

Deactivate the environment when finished:

```bash
deactivate
```

### Creating the virtual environment

Using `uv` keeps Python 3.12 and the dependencies isolated from the system
Python installation:

```bash
cd /path/to/jsk_demos/2026_07_narrative_chatbot
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

If `uv` is unavailable, consult the official
[uv installation documentation](https://docs.astral.sh/uv/getting-started/installation/).

## Environment Variables

### API key

Set the OpenAI API key in the current shell:

```bash
export OPENAI_API_KEY='YOUR_API_KEY'
```

Do not store the API key in source files, this README, or files tracked by Git.

To check whether the variable is set without displaying its value:

```bash
python -c "import os; print('set' if os.getenv('OPENAI_API_KEY') else 'unset')"
```

### Model configuration

The training/generation model and judge model can be configured independently:

```bash
export DSPY_TRAIN_MODEL='openai/gpt-4o'
export DSPY_JUDGE_MODEL='openai/gpt-4o-mini'
```

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | Authentication for the OpenAI API | Required |
| `DSPY_TRAIN_MODEL` | Model used for compilation and conversation generation | `openai/gpt-4o` |
| `DSPY_JUDGE_MODEL` | Model used by the semantic evaluator during compilation | `openai/gpt-4o-mini` |
| `DSPY_MODEL` | Compatibility setting used when `DSPY_TRAIN_MODEL` is unset | `openai/gpt-4o` |
| `POOH_CACHE_DIR` | Directory for compiled programs | `.dspy_cache` |
| `POOH_LOG_DIR` | Directory for conversation logs | `logs` |

Changing the model, examples, metric, or DSPy version produces a different compiled-program identifier and requires compilation for that configuration.

## Usage

Activate the virtual environment before running any command:

```bash
source .venv/bin/activate
```

### Scenarios

The agent, mishearing gimmick, and evaluators are shared across scenarios; only
the initial `NarrativeSituation` and full-turn examples change per scenario.
Available scenarios are registered in `scripts/scenarios.py`:

| `--scenario` | Content |
|---|---|
| `tea_party` (default) | Open-ended tea party with Pooh (`scripts/pooh_examples.py`) |
| `eeyore_birthday` | Eeyore-birthday-gift arc, with lines from the original story (`scripts/pooh_eeyore_examples.py`) |

In `eeyore_birthday`, once Pooh commits to giving Eeyore the honey jar, a
runtime timer starts. When the timer expires, the honey-eating event occurs
even while the program is waiting for participant input. A participant asking
Pooh not to eat it does not stop it; Pooh sidesteps the promise instead. The
chat output shows the DSPy-inferred scene as
`[参考場面]`; this label is for observation and logging only.

The delays are configured in one place near the top of `scripts/scenarios.py`:

```python
EEYORE_GIFT_DECISION_DELAY_SECONDS = 30.0
EEYORE_HONEY_TASTING_DELAY_SECONDS = 30.0
EEYORE_HONEY_EATING_DELAY_SECONDS = 10.0
EEYORE_EVENT_QUIET_SECONDS = 12.0
EEYORE_IDLE_CLOSE_AFTER_WRAP_UP_SECONDS = 30.0
```

The first value is the time from the end of the spoken opening until Pooh
decides on the honey jar himself. The second controls how long he waits before
taking out the jar, and the third controls how long he waits before eating its
honey. Every delay and quiet period counts from when Pooh's latest line is
expected to finish playing, estimated at `SPEECH_CHARS_PER_SECOND` (4.3,
measured from the fixed-utterance WAVs) in `scripts/narrative_events.py`.
The decision and the taking-out open new story beats, so they do not interrupt
an ongoing exchange: once due, each fires alone after `EEYORE_EVENT_QUIET_SECONDS`
of silence, or right after Pooh's next answer (introduced with a fixed
"あ、そうだ！" / "あ、そういえば。" line), whichever comes first. Eating follows
taking-out without waiting for silence. Once the participant and Pooh settle
what to do about the empty jar (give it as is, switch to another gift, and so
on), Pooh wraps up with a fixed line that reflects on
the gift and asks whether to keep talking; the session does not end there.
It ends when the participant leaves (or ends it explicitly), or after
`EEYORE_IDLE_CLOSE_AFTER_WRAP_UP_SECONDS` of silence following the wrap-up.
Changing only these runtime values
requires restarting chat mode but does not require DSPy recompilation.

Each scenario is compiled and cached separately, so compile the scenario you
intend to chat with before starting a session:

```bash
python scripts/pooh_narrative_dspy.py --mode compile --scenario eeyore_birthday
python scripts/pooh_narrative_dspy.py --mode chat --scenario eeyore_birthday
```

Omitting `--scenario` uses `tea_party`.

### Compile the DSPy program

Compile the program before the first chat session and after changing the examples, metric, or model configuration:

```bash
python scripts/pooh_narrative_dspy.py --mode compile
```

`BootstrapFewShot` uses the manually reviewed full-turn examples in `TRAINSET`
to evaluate candidate traces. Each Predictor's teacher receives examples matching
its own task: interaction classification, mishearing planning, or response
generation. Accepted traces are then merged with those task-specific examples.

A separate DSPy evaluator assesses candidate outputs along five dimensions:

1. Narrative coherence
2. Respect for participant intent
3. Participant agency
4. Openness for further engagement
5. State-update quality

Compilation makes multiple API requests and may take time and incur API charges. The resulting program is stored in `.dspy_cache`.

### Start a chat session

```bash
python scripts/pooh_narrative_dspy.py --mode chat
```

Chat mode uses only the compiled program matching the current configuration. It does not compile automatically.

Enter either speech or a physical action at the prompt:

```text
あなたの発話・行為: イーヨーのお皿も出しておこうか
```

Physical actions are entered as text:

```text
あなたの発話・行為: プーの頭をなでる
```

Enter `exit` to end the session:

```text
exit
```

Empty input is ignored with a prompt to try again. End-of-file input and Ctrl+C also terminate the session safely.

### Start the Web chat UI (optional)

The Web UI lets other people try the same compiled program from a browser.
The API key stays on the server, and each browser tab gets its own
`NarrativeSession` with independent story state, history, timers, and log file.

```bash
python scripts/web_server.py --scenario eeyore_birthday
```

Open `http://127.0.0.1:8080/`. To accept connections from other machines,
bind to all interfaces and require an access token so that strangers cannot
spend API credits:

```bash
export POOH_WEB_TOKEN='choose-a-long-random-string'
python scripts/web_server.py --scenario eeyore_birthday --host 0.0.0.0
```

The server refuses to start on a non-loopback host when `POOH_WEB_TOKEN` is
unset. It prints the URL including `?token=...`; share that URL.

- Timed world events are pushed to the browser even when the participant says
  nothing.
- Closing or reloading the tab does not end the story. The tab reconnects to
  the same session until `--session-ttl` seconds (default 600) of inactivity
  pass; an expired session is discarded without the ending line or motion.
- The **終了** button (or typing `exit`) plays the scenario's fixed ending
  line with its ending motion. When the model itself interprets an utterance
  as leaving, Pooh answers with the same fixed ending line and motion instead
  of a generated goodbye.
- **はじめから** ends the current session (with the ending line and motion, sent to ROS
  once, if the story was still running), frees its slot, and starts a new one.
- The **詳細** toggle shows the interaction mode, reference scene, narrative
  actions, and state changes for each turn.
- **過去ログ** lists the server's `logs/session_*.jsonl` files and opens a
  read-only turn viewer. It is shown only when the browser connects through
  `127.0.0.1` or `::1`; the server rejects log API access from other machines
  even if they know the URL or Web access token. Arbitrary filesystem paths
  cannot be opened.
- Each session loads its own copy of the compiled program, so different
  participants' LLM calls run concurrently; calls within one session are
  serialized. `--max-sessions` (default 20) limits concurrent sessions.
- `--ros-relay` also sends every Web turn to the ROS relay (output only; the
  relay's speech input is not read). Use it only when one participant drives
  the robot.

### Connect to the Pooh body (optional)

The chatbot remains a Python 3.12 application and does not import ROS. When
`--ros-relay` is specified, it receives recognized speech and sends each
generated response as newline-delimited JSON over one TCP connection to the
ROS-side bridge in the
[pooh_body package](https://gitlab.jsk.imi.i.u-tokyo.ac.jp/nagata/modular_robot_model_zoo/-/tree/add-pooh-model/pooh_body).
The bridge subscribes to `/speech_to_text_final`, sends its text to DSPy,
publishes response JSON on `/pooh_narrative_response`, selects a motion preset,
and executes the corresponding body motion. In relay mode, the terminal input
prompt is replaced by `/speech_to_text_final` input.

Start the bridge with the ROS system Python:

```bash
source /opt/ros/noetic/setup.bash
source ~/catkin_ws/devel/setup.bash
python3 ~/catkin_ws/src/modular_robot_model_zoo/pooh_body/scripts/narrative_motion_bridge.py
```

Then start the chatbot in the project `.venv`:

```bash
source .venv/bin/activate
python scripts/pooh_narrative_dspy.py \
  --mode chat \
  --scenario eeyore_birthday \
  --ros-relay
```

The bridge listens on `127.0.0.1:8765` by default. To inspect the generated
turns from another terminal:

```bash
rostopic echo /pooh_narrative_response
```

To send a text input through the same route without speaking:

```bash
rostopic pub -1 /speech_to_text_final std_msgs/String "data: '青い風船がいいな'"
```

See the [pooh_body operation guide](https://gitlab.jsk.imi.i.u-tokyo.ac.jp/nagata/modular_robot_model_zoo/-/blob/add-pooh-model/pooh_body/README.md)
for the robot-side setup and motion configuration.

Scripted opening, ending, and fixed-event lines carry a stable
`fixed_utterance_id`. The robot-side TTS node first looks for the corresponding
WAV and generates it only when it is missing. Every fixed line, its ID,
expected WAV filename, and optional body `motion` (e.g. the opening and ending
gestures) are listed in `config/fixed_utterances.json`; the relay sends that
motion with the line, so the robot side needs no copy of it.

### Write the session as a picture book (optional)

The session can be retold as a picture book, written live on the reMarkable
Paper Pro and reviewable afterwards. The book only retells what the session
recorded, without asking a model to invent anything:

- the participant's words (「…」と、あなたは いいました。);
- Pooh's lines;
- the body motion of fixed lines (プーは かなしそうな かおで、あごに てを あてて、…);
- the scripted world events, each opening a new illustrated page.

Pages break at those story beats and when the text no longer fits. Beats,
their narration and illustrations, and the motion phrases are configured in
`config/storybook.json`.

The tablet side is `storybook_viewer/`, an AppLoad app built on
[plush_memory](https://github.com/ayaha-n/plush_memory)'s e-ink viewer: it
uses that viewer's engine as a library (`plush_memory/eink-viewer`, see
"Reusing the viewer in another app" in plush_memory's README) and adds only
reading the finished book back. The chatbot pushes to it over ssh with
`plush_memory/scripts/eink_memory_push.py`. The tablet needs xovi and
AppLoad as for plush_memory. Build and install it once:

```bash
cd storybook_viewer && cargo build --release --target aarch64-unknown-linux-musl
ssh root@10.11.99.1 mkdir -p /home/root/xovi/exthome/appload/storybook_viewer
scp target/aarch64-unknown-linux-musl/release/storybook_viewer external.manifest.json icon.png \
    root@10.11.99.1:/home/root/xovi/exthome/appload/storybook_viewer/
# a tablet set down on its side (with xochitl's auto-rotate off):
ssh root@10.11.99.1 'echo landscape_cw > /home/root/xovi/exthome/appload/storybook_viewer/orientation.conf'
```

Then press reload in the AppLoad list, close any other AppLoad app, open
"Storybook", and start the chatbot:

```bash
export EINK_HOST=10.11.99.1   # USB; or the tablet's ssh alias
python scripts/pooh_narrative_dspy.py --mode chat --scenario eeyore_birthday \
  --ros-relay --storybook-eink
# or with the Web UI (the book follows the newest browser session):
python scripts/web_server.py --scenario eeyore_birthday --storybook-eink
```

`--storybook-preview DIR` writes the pages being written as PNGs instead of,
or as well as, the tablet, so the book can be checked without it. The page
layout follows the way the tablet is turned when a session starts: portrait
puts the picture above the text, landscape spreads them like an open book.

If the tablet is unreachable, the conversation goes on and the error is
printed. Writing speeds up while turns pile up, so the book stays close to
the conversation.

`plush_memory` is expected next to `jsk_demos` in the catkin workspace. Set
`PLUSH_MEMORY_DIR` if it lives elsewhere; the handwriting font
(`eink-viewer/fonts/Yomogi-Regular.ttf`) is also taken from there.

When a live book ends, it is saved as a PDF laid out as the tablet showed
it, in `storybooks/` in the log directory (`logs/storybooks/<log name>.pdf`
by default). The PDF holds the text as text in the handwriting font, so it
stays sharp and searchable; only the illustrations are images.

To read a session back:

- **On the tablet, right after the session:** once the last page is
  written, tap the right third of the panel for the next page and the left
  third for the previous one. Touches do nothing while a book is being
  written, and the next session's book starts afresh.
- **Web UI, right after the session:** 絵本を見る appears once it ends.
- **Web UI, any past session:** open 過去ログ and check 絵本で見る; choose
  横/縦 for the page orientation. PDF opens the saved live book, or renders
  one in the chosen orientation if there is none.
- **Files:**

  ```bash
  python scripts/storybook_render.py logs/session_<...>.jsonl             # -> logs/storybooks/<log name>.pdf
  python scripts/storybook_render.py logs/saved_examples/<...>.jsonl --layout portrait --out /tmp/book.pdf --png /tmp/pages
  ```

Illustrations are PNGs under `assets/storybook/<scenario>/`, at the paths
given in `config/storybook.json`, kept as 256-color PNGs to stay small (the
picture barely changes; the tablet's reveal is black and white anyway). A
scene whose illustration does not exist yet is laid out as text: in
landscape it continues on the right page if that is still empty.

`scripts/storybook_illustrations.py --scenario <key>` asks `gpt-image-1` to
draw them from each beat's prompt, with `assets/storybook/bear-reference.png`
as the reference. In practice the image model's moderation has rejected
these requests (`moderation_blocked` at the output stage), so drawn or
photographed illustrations may have to be supplied by hand.

### Compare implementation variants

```bash
python scripts/pooh_narrative_dspy.py --mode compare
```

Comparison mode displays outputs from the following three variants for the same input:

- `Predict`
- `ChainOfThought`
- `Compiled`

Use `--mode chat` for ordinary interaction sessions.

## Tests

Run the regression tests without calling an LLM or external API:

```bash
python -m unittest discover -s tests -v
```

Run a syntax check:

```bash
python -m py_compile \
  scripts/pooh_narrative_dspy.py \
  scripts/web_server.py \
  tests/test_pooh_narrative_dspy.py \
  tests/test_web_server.py
```

The current regression tests verify that:

- The initial state includes the required location, props, and participant role.
- Every example contains all declared output fields.
- History retains the raw action, interaction mode, response, and updated structured state.
- State deltas can update place and purpose, add or remove characters, and preserve untouched fields.
- Changing either model changes the compiled-program identifier.
- Judge scores are bounded between 1 and 5.

Testing actual model-output quality, state retention across generated turns, and the LLM evaluator inside `BootstrapFewShot` requires an integration test with a configured API key.

## State and History

After each turn, the LLM generates a structured `situation_update`. Python applies that delta to a deep copy of `current_situation`, producing the complete `updated_situation` used by the next turn.

The recent-turn history retains:

- Raw participant speech or action
- Selected interaction mode
- Pooh's response
- Updated situation

The application stores a complete structured situation. The LLM outputs only the fields that changed; Python preserves every untouched field when applying the update.

## Logs and Caches

### Conversation logs

Conversation logs are written to `logs/session_*.jsonl` by default. Each line contains one turn as a JSON object.

Recorded fields include:

- Timestamp
- Record source (`participant` or `world_event`) and raw input/event
- Observation-only scene ID and validated narrative actions
- Inferred participant intent
- Narrative interpretation
- Pooh's response
- Updated situation
- Model name
- Compiled-program identifier
- Response latency
- `fixed_utterance_id` of a scripted line (its motion is in `config/fixed_utterances.json`)

When a session that has logged turns is closed, a last `session_close` line
records the ending line and the reason, so the picture book of the log ends
where the session did.

The logs may contain participant speech, actions, or other research data. Before sharing, backing up, or analyzing them, confirm that their handling is consistent with the research protocol, consent scope, and applicable privacy requirements.

### Caches

The `.dspy_cache` directory stores:

- Compiled DSPy programs
- DSPy API-response caches

Both `.dspy_cache` and `logs` are excluded from Git.

## Repository Structure

```text
.
├── CODEX_INSTRUCTIONS_POOH_DSPY.md  # Research and implementation requirements
├── README.md                         # Setup and usage
├── requirements.txt                  # Python dependencies
├── scripts/                           # DSPy implementation and command-line entry point
│   ├── pooh_narrative_dspy.py
│   ├── narrative_events.py
│   ├── narrative_state.py
│   ├── scenarios.py
│   ├── web_server.py                 # Browser chat UI server
│   ├── storybook.py                  # Session -> picture-book pages (narration, layout)
│   ├── storybook_live.py             # Live book on the e-ink tablet / PNG preview
│   ├── storybook_render.py           # Past log -> page PNGs, PDF, book.json
│   ├── storybook_illustrations.py    # Illustration generation per story beat
│   └── ...
├── assets/storybook/                   # Picture-book illustrations per scenario
├── config/                            # Fixed lines and motions, picture-book settings
├── storybook_viewer/                   # AppLoad app on the tablet for the live book (Rust)
├── web/                               # Browser chat UI (HTML/JS/CSS)
└── tests/
    ├── test_pooh_narrative_dspy.py   # Regression tests without LLM calls
    ├── test_web_server.py            # Web UI server tests with a fake session
    └── test_storybook.py             # Picture-book tests without tablet or APIs
```
