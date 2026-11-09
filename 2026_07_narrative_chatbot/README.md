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
| [`docs/usage_details.md`](docs/usage_details.md) | Every command-line option, the robot protocol (in English) |
| [`docs/web_ui.md`](docs/web_ui.md) | What the Web UI exposes to whom, and publishing it with `--public` |
| [`docs/postcard.md`](docs/postcard.md) | Postcards made from a session's picture book |
| [`docs/printing.md`](docs/printing.md) | Printing the postcards on the SELPHY: printer, paper, setting up another PC |
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

The agent, mishearing gimmick, and evaluators are shared across scenarios. Scenarios are registered in `scripts/scenarios.py`:

| `--scenario` | Content |
|---|---|
| `tea_party` (default) | Open-ended tea party with Pooh (`scripts/pooh_examples.py`) |
| `eeyore_birthday` | Eeyore-birthday-gift arc, with lines from the original story (`scripts/pooh_eeyore_examples.py`, `scripts/eeyore_birthday_events.py`) |
| `footprints` | "Hunting the Woozle": Pooh and the participant track mysterious footprints that turn out to be their own (`scripts/pooh_footprints_examples.py`, `scripts/footprints_events.py`) |

In `eeyore_birthday` and `footprints`, timed world events move the story on even while the participant is silent (Pooh decides on the honey jar and eats it regardless of a request not to; the footprint count escalates and Pooh eventually hints at, then states, the twist). The delays are constants near the top of `scripts/scenarios.py` (changing them needs a restart, not recompilation); how and when the events fire is described in [`docs/timed_narrative_events.md`](docs/timed_narrative_events.md). The chat output shows the DSPy-inferred scene as `[参考場面]`, for observation and logging only.

### Adding a new scenario

A scenario's own content lives entirely in its own files; `scripts/pooh_narrative_dspy.py`, `scripts/narrative_events.py` and `scripts/narrative_signatures.py` stay scenario-agnostic (see [`docs/known_issues.md`](docs/known_issues.md) for how this separation was enforced — a new scenario should never need to touch them). To add one:

1. **`scripts/pooh_<name>_examples.py`** (new file) — the initial `NarrativeSituation`, the opening line, `<NAME>_TRAINSET` (full-turn examples built with `pooh_examples.example()`/`example_variants()`), and the derived `<NAME>_MODE_EXAMPLES` / `<NAME>_INTERPRET_EXAMPLES` / `<NAME>_RESPONSE_EXAMPLES` (via `pooh_examples.build_mode_examples()` / `build_interpret_examples()` / `build_response_examples()`). Add `pooh_preferences` (a dict keyed by an unresolved-item label) only if Pooh needs a fallback opinion of his own on some topic.
2. **`scripts/<name>_events.py`** (new file, only if the scenario needs timed world events or its own closed `narrative_actions` vocabulary) — modeled on `eeyore_birthday_events.py` or `footprints_events.py`:
   - A state `@dataclass` and a `NarrativeEventController` subclass with the scenario's `RequiredNarrativeEvent`s (delays, prerequisites, fixed lines).
   - Any scenario-specific `AnalyzeInteraction` / `InterpretTurn` / `GeneratePoohResponse` subclass, built on the generic ones in `scripts/narrative_signatures.py` (a plain subclass to add fields; see that module's own classes' docstrings for when a `type()`-built subclass is needed instead, to extend instructions without losing the base text).
   - Any of the controller's hooks worth overriding: `fixed_line_for` (bypass generation with a fixed line, decided from the classify stage — use this instead of a keyword-matched guard, which does not generalize), `response_may_be_replaced` / `enforce_response_invariants` (post-hoc guards for cases `fixed_line_for` cannot catch before generation), `extra_actions_for`, `synchronize_situation` (render controller-owned state into the narrative situation deterministically, as a backstop against the LM's own `situation_update` getting it wrong).
3. **`scripts/scenarios.py`** — add a `_<name>()` function building a `Scenario(...)` (see its field comments for what each one does — `classify_signature`/`interpret_signature`/`response_signature` only need setting if step 2 defined one), register it in the `SCENARIOS` dict (this alone makes `--scenario <name>` available everywhere: chat, compile, the Web UI), and add any new delay constants near the top of the file.
4. **`config/fixed_utterances.json`** — an entry (`text`, optionally `motion`) for every fixed line referenced from step 2; the robot side generates the WAV on demand.
5. **`config/storybook.json`** (optional) — a `scenarios.<name>` entry (`title`, `beats` keyed by the same world-event IDs used in step 2, `closing`) if the scenario should have a picture book; see [`docs/storybook.md`](docs/storybook.md).
6. Compile (`--mode compile --scenario <name>`) and run the full test suite. If step 2 added a controller, test it directly (construct it, feed it fake actions/classifications) the way `tests/test_pooh_narrative_dspy.py` does for `HoneyGiftEventController`.

## Usage

1. Compile each scenario once, and again after changing its examples, the metric or the models (many API requests; stored in `.dspy_cache`):

   ```bash
   python scripts/pooh_narrative_dspy.py --mode compile --scenario eeyore_birthday
   ```

2. Start it from the menu:

   ```bash
   python scripts/run.py
   ```

   Choose **Web UI** or **ロボット** on the first line, change only what differs from the shared defaults (`config/run_defaults.json`), and press `s`. It offers only the options this version has, shows the command it will run, and warns before starting about a port already in use, a TTS server or ROS relay that does not answer, or an empty passphrase. `--dry-run` only prints the command.

| | Web UI | Robot |
|---|---|---|
| Runs | `scripts/web_server.py` | `scripts/pooh_narrative_dspy.py --ros-relay` (no Web UI) |
| Participants | type in a browser: this PC, or anyone with the link when published | speak to the robot |
| Menu switches | publish, crowdsourcing (passphrase, minutes until the end button), illustrations, TTS server, tablet | how a story starts (on the robot's cue every time, or once at launch), other scenarios to offer, keep going after the wrap-up, tablet, illustrations, where the bridge runs |

For the robot, start the ROS-side bridge first, in a terminal without the `.venv`. pooh_body's `pooh_operator.launch use_narrative:=true` starts the bridge and the chatbot itself; the menu then refuses to start the robot, as a chatbot on the robot is already running.

```bash
source /opt/ros/noetic/setup.bash && source ~/catkin_ws/devel/setup.bash
python3 ~/catkin_ws/src/modular_robot_model_zoo/pooh_body/scripts/narrative_motion_bridge.py
```

To print a session's postcards on the SELPHY (newest first, with a preview): `python scripts/print_postcards.py`; see [`docs/printing.md`](docs/printing.md).

To chat in the terminal instead: `python scripts/pooh_narrative_dspy.py --mode chat --scenario eeyore_birthday` (`exit` or Ctrl+C ends it).

Each option by hand, what participants can reach, the robot protocol and the picture book: [`docs/usage_details.md`](docs/usage_details.md), [`docs/web_ui.md`](docs/web_ui.md) (publishing, crowdsourcing, TTS) and [`docs/storybook.md`](docs/storybook.md).

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
│   ├── run.py                       # Menu to start the Web UI or the robot
│   ├── web_server.py                # Web chat UI server
│   ├── public_tunnel.py             # Cloudflare quick tunnel for --public
│   ├── storybook.py                 # Session -> picture-book pages (narration, layout)
│   ├── storybook_live.py            # Live book on the tablet / PNG preview
│   ├── storybook_render.py          # Log -> PDF (and page PNGs)
│   ├── storybook_illustrations.py   # Illustration generation per scene
│   ├── postcard.py                  # Book -> printable postcards (PDF)
│   ├── print_postcards.py           # Postcards -> the SELPHY printer, borderless
│   └── ...
├── config/                          # Fixed lines and motions, picture-book settings
├── assets/storybook/                # Picture-book illustrations per scenario
├── assets/fonts/                    # The handwriting font (Yomogi, OFL)
├── storybook_viewer/                # Tablet app for the live book (Rust, AppLoad)
├── web/                             # Web chat UI (HTML/JS/CSS)
├── docs/                            # Design notes
└── tests/                           # Tests without LLM calls, APIs or the tablet
```
