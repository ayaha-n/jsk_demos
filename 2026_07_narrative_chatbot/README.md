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
export DSPY_TRAIN_MODEL='openai/gpt-4o-mini'
export DSPY_JUDGE_MODEL='openai/gpt-4o-mini'
```

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | Authentication for the OpenAI API | Required |
| `DSPY_TRAIN_MODEL` | Model used for compilation and conversation generation | `openai/gpt-4o-mini` |
| `DSPY_JUDGE_MODEL` | Model used by the semantic evaluator during compilation | Same as the training model |
| `DSPY_MODEL` | Compatibility setting used when `DSPY_TRAIN_MODEL` is unset | `openai/gpt-4o-mini` |
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
runtime timer starts. If Pooh still has access to the full jar when the timer
expires, the honey-eating event occurs even while the program is waiting for
participant input. The chat output shows the DSPy-inferred scene as
`[参考場面]`; this label is for observation and logging only.

The delays are configured in one place near the top of `scripts/scenarios.py`:

```python
EEYORE_EVENT_INACTIVITY_DELAY_SECONDS = 30.0
EEYORE_HONEY_TASTING_DELAY_SECONDS = 30.0
EEYORE_HONEY_EATING_DELAY_SECONDS = 10.0
```

The first value controls how long Pooh waits for participant input before
deciding on the honey jar himself. The second controls how long he waits before
taking out the jar, and the third controls how long he waits before eating its
honey. Changing only these runtime values requires restarting chat mode but
does not require DSPy recompilation.

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
  tests/test_pooh_narrative_dspy.py
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
│   └── ...
└── tests/
    └── test_pooh_narrative_dspy.py   # Regression tests without LLM calls
```
