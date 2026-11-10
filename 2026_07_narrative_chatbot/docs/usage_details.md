# Usage details

What `python scripts/run.py` (see the README) does not explain: running the scripts by hand, the Web UI's behaviour, and how the chatbot talks to the robot. Publishing, crowdsourcing and TTS are in [`web_ui.md`](web_ui.md); the picture book is in [`storybook.md`](storybook.md); how compilation works is in [`system_architecture.md`](system_architecture.md).

## Compare the program's stages

To compare the `Predict`, `ChainOfThought` and `Compiled` outputs for one input:

```bash
python scripts/pooh_narrative_dspy.py --mode compare
```

## Web chat UI

Each browser tab gets its own session (story state, history, timers and log); the API key stays on the server. Who can join:

| Who can join | Command | URL to share |
|---|---|---|
| This PC only | `python scripts/web_server.py --scenario eeyore_birthday` | `http://127.0.0.1:8080/` |
| Also the same network (Wi-Fi, LAN) | `POOH_WEB_TOKEN='a-long-random-string' python scripts/web_server.py --scenario eeyore_birthday --host 0.0.0.0` | `http://<this PC's address>:8080/?token=...` (printed) |
| Also outside the network | `python scripts/web_server.py --scenario eeyore_birthday --public` | `https://<random>.trycloudflare.com/?token=...` (printed) |

Without `--public`, the server refuses a non-loopback `--host` unless `POOH_WEB_TOKEN` is set. What each kind of visitor can reach, and publishing safely: [`web_ui.md`](web_ui.md).

- A fresh tab shows only the scene and an **はじめる** button; the story (and its opening line's audio) starts on that tap, not on page load.
- Timed world events reach the browser even when the participant says nothing.
- Closing or reloading the tab does not end the story; it reconnects until `--session-ttl` seconds (default 600) of inactivity, after which the session is dropped without the ending.
- **終了** (or `exit`) plays the scenario's fixed ending line and motion, as does the model judging that the participant is leaving. **はじめから** ends the current session the same way and starts a new one (hidden under `--crowdsourcing`).
- **詳細** (each turn's mode, scene, actions and state changes) and **過去ログ** work only from this PC (`127.0.0.1` / `::1`), whatever the token.
- Sessions run their LLM calls concurrently (calls within one session are serialized); `--max-sessions` (default 20) caps them.
- `--ros-relay` also sends every Web turn to the robot (output only); use it with a single participant.

## Talking to the robot

The chatbot stays a plain Python 3.12 application without ROS. With `--ros-relay`, it takes recognized speech from, and sends each response as newline-delimited JSON to, the ROS-side bridge `narrative_motion_bridge.py` in [pooh_body](https://gitlab.jsk.imi.i.u-tokyo.ac.jp/nagata/modular_robot_model_zoo/-/tree/add-pooh-model/pooh_body) over TCP (`127.0.0.1:8765` by default). The bridge subscribes to `/speech_to_text_final`, publishes the responses on `/pooh_narrative_response`, and moves the body.

To watch the turns, or to send text instead of speaking, use a ROS terminal:

```bash
rostopic echo /pooh_narrative_response
rostopic pub -1 /speech_to_text_final std_msgs/String "data: '青い風船がいいな'"
```

Scripted lines (opening, ending, fixed events) carry a stable `fixed_utterance_id`; `config/fixed_utterances.json` lists each one's text, WAV file and body `motion`, which the relay sends with the line. The robot's TTS plays the WAV, generating it only if missing. See the [pooh_body guide](https://gitlab.jsk.imi.i.u-tokyo.ac.jp/nagata/modular_robot_model_zoo/-/blob/add-pooh-model/pooh_body/README.md) for the robot side.

A generated line is sent sentence by sentence while it is written (`narrative_response_chunk`, numbered by `index`), so the robot starts speaking before the turn is finished; the complete `narrative_response` that follows carries `"streamed": true` so that it is not spoken again. A turn whose line may still be replaced (an exit, the guards in `enforce_response_invariants`; see `response_may_be_replaced`) is sent whole at the end. A participant's request about the honey (not to eat it, or to give something else instead of Pooh's jar) is judged before generation, and answered with a fixed line instead.

The ROS side may also report what the robot saw the participant do (e.g. `参加者がはちみつの壺をプーの左手に手渡した`, from pooh_body's `handover_vision_relay.py`), with an utterance or on its own as `observed_actions`. These are added to the story's events without a reply of their own, logged with source `observed`, and retold in the picture book (with what was said, if they came with an utterance).

With `--start-on-mode`, the chatbot follows the robot's interaction mode: entering the narrative mode starts a story with the opening line, leaving it ends the story with the ending line, and entering it again starts a running story over. The chatbot sends each scenario's start cues (`start_cues` in `scripts/scenarios.py`, e.g. 「イーヨ」 and 「相談」 for 「プー、今日はイーヨーについて相談したいことがあるんだよね」) to the bridge, which turns to the narrative mode when it hears one, and back to free talk when a story ends with its ending line. `--scenarios` selects the scenarios the robot side can choose (space-separated, or `all`). The chatbot keeps running between stories, waits for the bridge if it is not up yet, and is ended with Ctrl+C. Without `--start-on-mode`, one story starts at once and the program ends with it. `--no-idle-close` keeps a story going after the silence that follows its wrap-up.

The operator dashboard uses `narrative_scenarios` from `pooh_operator.launch`.
Its default is `all` (all registered scenarios, birthday story initially selected).
Specify one name or a space-separated list to load only those scenarios; the
first is initially selected. Each needs its compiled program on that PC.
The equivalent chatbot option is `--scenarios`, used with
`--ros-relay --start-on-mode`. Repeated names are loaded only once.
The bridge publishes the loaded list on the latched
`/pooh_narrative_scenarios` topic, so a dashboard opened later also shows
the same choices. The old operator launch arguments `narrative_scenario`
and `narrative_extra_scenarios` are replaced by `narrative_scenarios`.
Single-scenario CLI uses, including compilation, still use `--scenario`.

Robot launches and the robot menu enable postcards by default; the Web
default stays off. The operator dashboard's print button follows the
latest robot session's receipt (`.tools/robot_postcards.json`), showing
preparation, failure or readiness. The button always opens a window where
completed past sessions can also be selected, even while current cards are
still being made. The current PDF is offered only once ready, separately
labeled from past sessions. A preview has one checkbox per card for
multiple selection and confirmation, then passes the selected session's
explicit PDF and selected card numbers to `print_postcards.py`, never the
newest PDF from a directory. See [printing.md](printing.md).

```bash
# All scenarios (the default):
roslaunch pooh_body pooh_operator.launch use_narrative:=true
# Select only these two; initially select the birthday story:
roslaunch pooh_body pooh_operator.launch use_narrative:=true \
  narrative_scenarios:="eeyore_birthday footprints"
# Footprints only:
roslaunch pooh_body pooh_operator.launch use_narrative:=true \
  narrative_scenarios:=footprints
```
