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

### Compile the DSPy program

Compile the program before the first chat session and after changing the examples, metric, or model configuration:

```bash
python pooh_narrative_dspy.py --mode compile
```

`BootstrapFewShot` optimizes the program from the manually prepared examples. A separate DSPy evaluator assesses candidate outputs along five dimensions:

1. Narrative coherence
2. Respect for participant intent
3. Participant agency
4. Openness for further engagement
5. State-update quality

Compilation makes multiple API requests and may take time and incur API charges. The resulting program is stored in `.dspy_cache`.

### Start a chat session

```bash
python pooh_narrative_dspy.py --mode chat
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

### Compare implementation variants

```bash
python pooh_narrative_dspy.py --mode compare
```

Comparison mode displays outputs from the following three variants for the same input:

- `Predict`
- `ChainOfThought`
- `Compiled`

Use `--mode chat` for ordinary interaction sessions.

## Tests

Run the regression tests without calling an LLM or external API:

```bash
python -m unittest -v test_pooh_narrative_dspy.py
```

Run a syntax check:

```bash
python -m py_compile \
  pooh_narrative_dspy.py \
  test_pooh_narrative_dspy.py
```

The current regression tests verify that:

- The initial state includes the required location, props, and participant role.
- Every example contains all declared output fields.
- History retains the raw action, inferred intent, interpretation, response, and updated state.
- Changing either model changes the compiled-program identifier.
- Judge scores are bounded between 1 and 5.

Testing actual model-output quality, state retention across generated turns, and the LLM evaluator inside `BootstrapFewShot` requires an integration test with a configured API key.

## State and History

After each turn, the generated `updated_situation` becomes the next turn's `current_situation`.

The recent-turn history retains:

- Raw participant speech or action
- Inferred participant intent
- Narrative interpretation
- Pooh's response
- Updated situation

The updated situation is designed to be self-contained rather than a short description of only what changed.

## Logs and Caches

### Conversation logs

Conversation logs are written to `logs/session_*.jsonl` by default. Each line contains one turn as a JSON object.

Recorded fields include:

- Timestamp
- Raw participant input
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
├── pooh_narrative_dspy.py            # Main DSPy program
├── requirements.txt                  # Python dependencies
└── test_pooh_narrative_dspy.py       # Regression tests without LLM calls
```
