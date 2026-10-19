# Narrative Interaction with Pooh Using DSPy

This research prototype uses DSPy to interpret a participant's open-ended speech and physical actions in relation to a shared tea-party scene.

It is not intended to be a general-purpose character chatbot or a reproduction of the original stories. Its purpose is to support both:

- **Narrative coherence:** consistency with the current scene, previous events, characters, props, and goals.
- **Participant agency:** preserving the participant's actions and leaving subsequent actions open to their own choice.

See [`CODEX_INSTRUCTIONS_POOH_DSPY.md`](CODEX_INSTRUCTIONS_POOH_DSPY.md) for the full research and implementation requirements. Design notes and details are in `docs/` (in Japanese):

| Document | Contents |
|---|---|
| [`docs/system_architecture.md`](docs/system_architecture.md) | How compilation and chat are put together, with flowcharts |
| [`docs/common_response_policy.md`](docs/common_response_policy.md) | Response policy shared by all scenarios |
| [`docs/eeyore_scenario_flow.md`](docs/eeyore_scenario_flow.md) | The Eeyore-birthday scenario, scene by scene |
| [`docs/eeyore_examples_overview.md`](docs/eeyore_examples_overview.md) | The `eeyore_birthday` training examples |
| [`docs/timed_narrative_events.md`](docs/timed_narrative_events.md) | Timed world events: delays and when they fire |
| [`docs/web_ui.md`](docs/web_ui.md) | What the Web UI exposes to whom, and publishing it with `--public` |
| [`docs/storybook.md`](docs/storybook.md) | The picture book: layout, the tablet app, reading back, illustrations |
| [`docs/known_issues.md`](docs/known_issues.md) | Unverified concerns and open questions |

## Setup

Requirements: Linux, Python 3.12, DSPy 3.2.1, an OpenAI API key, and internet access (for installation, compilation and inference).

Writing the picture book on the reMarkable tablet also needs [plush_memory](https://github.com/ayaha-n/plush_memory) (its `feature/eink-display` branch, for now), checked out in the same catkin workspace as `jsk_demos` (`~/catkin_ws/src/plush_memory`), and a Rust toolchain to build the tablet app. Everything else, including the picture book's PDFs and Web view, works without it:

```bash
cd ~/catkin_ws/src && git clone -b feature/eink-display https://github.com/ayaha-n/plush_memory.git
```

Use a project-local virtual environment (`.venv`, excluded from Git), for example with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
cd /path/to/jsk_demos/2026_07_narrative_chatbot
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate        # before the commands below (not the ROS-side bridge); `deactivate` to leave
```

Set the API key in the shell; never store it in files tracked by Git:

```bash
export OPENAI_API_KEY='YOUR_API_KEY'
```

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | Authentication for the OpenAI API | Required |
| `DSPY_TRAIN_MODEL` | Model used for compilation and conversation generation | `openai/gpt-4o` |
| `DSPY_JUDGE_MODEL` | Model used by the semantic evaluator during compilation | `openai/gpt-4o-mini` |
| `DSPY_MODEL` | Compatibility setting used when `DSPY_TRAIN_MODEL` is unset | `openai/gpt-4o` |
| `POOH_CACHE_DIR` | Directory for compiled programs | `.dspy_cache` |
| `POOH_LOG_DIR` | Directory for conversation logs | `logs` |
| `POOH_WEB_TOKEN` | Access token for the Web UI beyond this PC | (none) |
| `EINK_HOST` | The reMarkable tablet for the picture book | `10.11.99.1` |
| `PLUSH_MEMORY_DIR` | plush_memory, if not in the same catkin workspace | `<workspace>/src/plush_memory` |

Changing the model, examples, metric, or DSPy version produces a different compiled-program identifier and requires compiling again.

## Scenarios

The agent, mishearing gimmick, and evaluators are shared across scenarios; only the initial `NarrativeSituation` and full-turn examples change. Scenarios are registered in `scripts/scenarios.py`:

| `--scenario` | Content |
|---|---|
| `tea_party` (default) | Open-ended tea party with Pooh (`scripts/pooh_examples.py`) |
| `eeyore_birthday` | Eeyore-birthday-gift arc, with lines from the original story (`scripts/pooh_eeyore_examples.py`) |

In `eeyore_birthday`, timed world events move the story on even while the participant is silent: Pooh decides on the honey jar, takes it out, eats the honey, and finally wraps up once the empty jar is settled; a request not to eat it does not stop it. The delays are constants near the top of `scripts/scenarios.py` (changing them needs a restart, not recompilation); how and when the events fire is described in [`docs/timed_narrative_events.md`](docs/timed_narrative_events.md). The chat output shows the DSPy-inferred scene as `[参考場面]`, for observation and logging only.

## Usage

### Compile the DSPy program

Compile each scenario once before chatting, and again after changing the examples, metric, or models:

```bash
python scripts/pooh_narrative_dspy.py --mode compile --scenario eeyore_birthday
```

`BootstrapFewShot` evaluates candidate traces against the manually reviewed full-turn examples in `TRAINSET`; each Predictor's teacher gets examples for its own task (interaction classification, mishearing planning, response generation). A separate DSPy evaluator scores narrative coherence, respect for participant intent, participant agency, openness for further engagement, and state-update quality. Compilation makes many API requests (time and charges); the program is stored in `.dspy_cache`. See [`docs/system_architecture.md`](docs/system_architecture.md).

### Chat in the terminal

```bash
python scripts/pooh_narrative_dspy.py --mode chat --scenario eeyore_birthday
```

Chat mode only uses an already compiled program. Type speech or a physical action as text (`イーヨーのお皿も出しておこうか`, `プーの頭をなでる`); `exit`, end-of-file or Ctrl+C ends the session.

To compare the `Predict`, `ChainOfThought` and `Compiled` outputs for one input:

```bash
python scripts/pooh_narrative_dspy.py --mode compare
```

### Web chat UI

The Web UI lets other people try the same program from a browser. The API key stays on the server, and each browser tab gets its own session (story state, history, timers and log).

Choose one of these, by who should reach it (add `--storybook-eink`, `--ros-relay` etc. to any):

| Who can join | Command | URL to share |
|---|---|---|
| This PC only | `python scripts/web_server.py --scenario eeyore_birthday` | `http://127.0.0.1:8080/` |
| Also the same network (Wi-Fi, LAN) | `POOH_WEB_TOKEN='a-long-random-string' python scripts/web_server.py --scenario eeyore_birthday --host 0.0.0.0` | `http://<this PC's address>:8080/?token=...` (printed) |
| Also outside the network | `python scripts/web_server.py --scenario eeyore_birthday --public` | `https://<random>.trycloudflare.com/?token=...` (printed) |

`--public` needs no `--host`: the tunnel reaches the server on this PC, and the key is `POOH_WEB_TOKEN` or a fresh random one. Without `--public`, the server refuses a non-loopback `--host` unless `POOH_WEB_TOKEN` is set. Through `--host` or `--public`, participants get the conversation and their own picture book, not the past logs or the tablet; read [`docs/web_ui.md`](docs/web_ui.md) before publishing.

- Timed world events reach the browser even when the participant says nothing.
- Closing or reloading the tab does not end the story; it reconnects until `--session-ttl` seconds (default 600) of inactivity, after which the session is dropped without the ending.
- **終了** (or `exit`) plays the scenario's fixed ending line and motion, as does the model judging that the participant is leaving. **はじめから** ends the current session the same way and starts a new one.
- **詳細** shows each turn's interaction mode, reference scene, narrative actions and state changes.
- **過去ログ** opens a read-only viewer of `logs/session_*.jsonl`. It, and the log API, work only from this PC (`127.0.0.1` / `::1`), whatever the token.
- Sessions run their LLM calls concurrently (calls within one session are serialized); `--max-sessions` (default 20) caps them.
- `--ros-relay` also sends every Web turn to the robot (output only); use it with a single participant.

### Connect to the Pooh body

The chatbot stays a plain Python 3.12 application without ROS. With `--ros-relay`, it takes recognized speech from, and sends each response as newline-delimited JSON to, the ROS-side bridge in [pooh_body](https://gitlab.jsk.imi.i.u-tokyo.ac.jp/nagata/modular_robot_model_zoo/-/tree/add-pooh-model/pooh_body) over TCP (`127.0.0.1:8765`). The bridge subscribes to `/speech_to_text_final`, publishes the responses on `/pooh_narrative_response`, and moves the body.

Run the two sides in separate terminals. The bridge keeps running, and it needs ROS's system Python, so start it in a terminal without the `.venv`:

```bash
# Terminal 1: the ROS-side bridge (system Python, no .venv)
source /opt/ros/noetic/setup.bash && source ~/catkin_ws/devel/setup.bash
python3 ~/catkin_ws/src/modular_robot_model_zoo/pooh_body/scripts/narrative_motion_bridge.py
```

```bash
# Terminal 2: the chatbot (.venv)
source .venv/bin/activate
python scripts/pooh_narrative_dspy.py --mode chat --scenario eeyore_birthday --ros-relay
```

To watch the turns, or to send text instead of speaking, use a ROS terminal:

```bash
rostopic echo /pooh_narrative_response
rostopic pub -1 /speech_to_text_final std_msgs/String "data: '青い風船がいいな'"
```

Scripted lines (opening, ending, fixed events) carry a stable `fixed_utterance_id`; `config/fixed_utterances.json` lists each one's text, WAV file and body `motion`, which the relay sends with the line. The robot's TTS plays the WAV, generating it only if missing. See the [pooh_body guide](https://gitlab.jsk.imi.i.u-tokyo.ac.jp/nagata/modular_robot_model_zoo/-/blob/add-pooh-model/pooh_body/README.md) for the robot side.

A generated line is sent to the relay as soon as the respond stage has written it (`narrative_response_chunk`), so the robot starts speaking before the turn's last stage (interpret) finishes; the complete `narrative_response` that follows carries `"streamed": true` so that it is not spoken again. A turn whose line may still be replaced after that (an exit, the guards in `enforce_response_invariants`) is sent whole at the end as before; see `response_may_be_replaced`. A participant's request about the honey (not to eat it, or to give something else instead of Pooh's jar) is judged by the classify stage before generation, and while a fixed line answers it, no line is generated.

The ROS side may also report what the robot saw the participant do (e.g. `参加者がはちみつの壺をプーの左手に手渡した`, from pooh_body's `handover_vision_relay.py`), with an utterance or on its own as `observed_actions`. These are added to the story's events without a reply of their own, before the utterance, if any, is answered; they are logged with source `observed` and retold in the picture book.

### Picture book

The session can be retold as a picture book, written by hand live on a reMarkable Paper Pro and read back afterwards. It retells only what the session recorded (the participant's words, Pooh's lines, the motion of fixed lines and the timed events), with an illustrated page per scene. Install the tablet app `storybook_viewer/` once and open "Storybook" in AppLoad (see [`docs/storybook.md`](docs/storybook.md)), then add `--storybook-eink`:

```bash
python scripts/web_server.py --scenario eeyore_birthday --storybook-eink
```

- With the Web UI, only sessions started from a browser on this PC are written on the tablet.
- When a book ends it is saved as a PDF in `logs/storybooks/`. Read it back by tapping the sides of the tablet, with **絵本を見る** in the Web UI (each participant sees only their own book), with 過去ログ → 絵本で見る, or with `python scripts/storybook_render.py <log>`.
- `--storybook-preview DIR` writes the pages as PNGs, for working without the tablet.
- Scenes, narration, illustrations (`assets/storybook/`) and motion phrases are set in `config/storybook.json`; the handwriting font is bundled in `assets/fonts/`.
- Add `--storybook-generate` to generate session-specific illustrations in the background, or render a log with `scripts/storybook_render.py <log> --generate-illustrations`.
- Only the tablet needs [plush_memory](https://github.com/ayaha-n/plush_memory) (see Setup); PDFs, 絵本を見る and 過去ログ work without it.

## State, logs and caches

After each turn the LLM outputs only the changed fields of the situation (`situation_update`); Python applies them to a copy of the full `current_situation`, preserving everything else. The recent-turn history keeps the raw input, interaction mode, Pooh's response and the updated situation.

Each session writes `logs/session_*.jsonl`, one JSON object per turn: timestamp, source (`participant` or `world_event`) and raw input or event, scene ID and validated narrative actions, interpretation, Pooh's response and `fixed_utterance_id`, the updated situation, model, compiled-program identifier and latency. A closed session that has logged turns ends with a `session_close` line. The logs may contain participant speech and other research data: handle them within the research protocol, consent scope and privacy requirements.

`.dspy_cache` holds the compiled programs and DSPy's API-response cache. `.dspy_cache`, `logs` and `.tools` are excluded from Git.

## Tests

The tests use no LLM, external API or tablet:

```bash
python -m unittest discover -s tests -v
```

Model-output quality, state retention across generated turns, and the LLM evaluator inside `BootstrapFewShot` need an integration test with an API key.

## Repository Structure

```text
.
├── CODEX_INSTRUCTIONS_POOH_DSPY.md  # Research and implementation requirements
├── requirements.txt
├── scripts/
│   ├── pooh_narrative_dspy.py       # Agent, session, terminal chat, compile
│   ├── narrative_events.py          # Timed world events, fixed lines
│   ├── narrative_state.py           # NarrativeSituation and its updates
│   ├── scenarios.py                 # Scenario registry and event delays
│   ├── web_server.py                # Web chat UI server
│   ├── public_tunnel.py             # Cloudflare quick tunnel for --public
│   ├── storybook.py                 # Session -> picture-book pages (narration, layout)
│   ├── storybook_live.py            # Live book on the tablet / PNG preview
│   ├── storybook_render.py          # Log -> PDF (and page PNGs)
│   ├── storybook_illustrations.py   # Illustration generation per scene
│   └── ...
├── config/                          # Fixed lines and motions, picture-book settings
├── assets/storybook/                # Picture-book illustrations per scenario
├── assets/fonts/                    # The handwriting font (Yomogi, OFL)
├── storybook_viewer/                # Tablet app for the live book (Rust, AppLoad)
├── web/                             # Web chat UI (HTML/JS/CSS)
├── docs/                            # Design notes
└── tests/                           # Tests without LLM calls, APIs or the tablet
```
