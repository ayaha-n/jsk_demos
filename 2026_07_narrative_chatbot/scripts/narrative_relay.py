"""Bidirectional TCP client for the optional narrative ROS relay."""

from __future__ import annotations

import json
import select
import socket
import sys
from typing import Any

from narrative_events import fixed_utterance_motion


RECEIVE_POLL_SECONDS = 0.2


class NarrativeRelayPublisher:
    """Exchange participant input and narrative turns with the ROS relay."""

    def __init__(self, host: str, port: int, scenario_key: str) -> None:
        self.host = host
        self.port = port
        self.scenario_key = scenario_key
        self._socket: socket.socket | None = None
        self._receive_buffer = b""
        self._observed: list[str] = []
        # The robot's interaction mode (/pooh_interaction_mode) as the ROS
        # side last reported it; None until it does.
        self.narrative_mode: bool | None = None
        # How many times the narrative mode has been reported: each is a
        # request for a new story (the dashboard button pressed again too).
        self.narrative_entries = 0
        # Reported once until the relay is reached, as it is retried.
        self._unreachable_reported = False

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def _connect(self) -> socket.socket | None:
        if self._socket is not None:
            return self._socket
        try:
            self._socket = socket.create_connection((self.host, self.port), timeout=2.0)
            self._receive_buffer = b""
            if self._unreachable_reported:
                print(f"ROS中継({self.host}:{self.port})に接続しました", file=sys.stderr)
            self._unreachable_reported = False
        except OSError as error:
            if not self._unreachable_reported:
                print(
                    f"ROS中継({self.host}:{self.port})に接続できません: {error}",
                    file=sys.stderr,
                )
                self._unreachable_reported = True
            self._socket = None
        return self._socket

    def publish(
        self,
        *,
        bot_response: str,
        narrative_actions: list[str],
        scene_id: str,
        source: str,
        world_event_id: str | None = None,
        fixed_utterance_id: str | None = None,
        turn_id: str | None = None,
        streamed: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "narrative_response",
            "scenario": self.scenario_key,
            "bot_response": bot_response,
            "narrative_actions": narrative_actions,
            "scene_id": scene_id,
            "source": source,
        }
        if turn_id is not None:
            payload["turn_id"] = turn_id
        if streamed:
            # Its line was sent with publish_chunk as soon as it was written.
            payload["streamed"] = True
        if world_event_id is not None:
            payload["world_event_id"] = world_event_id
        if fixed_utterance_id is not None:
            payload["fixed_utterance_id"] = fixed_utterance_id
            # The robot plays the motion registered with the fixed line, so
            # config/fixed_utterances.json stays the one place to define it.
            motion = fixed_utterance_motion(fixed_utterance_id)
            if motion:
                payload["motion"] = motion
        self._send(payload)

    def publish_chunk(self, *, turn_id: str, index: int, text: str) -> bool:
        """Send a line before its turn finishes, so the robot starts speaking."""
        return self._send({
            "type": "narrative_response_chunk",
            "turn_id": turn_id,
            "index": index,
            "text": text,
        })

    def _send(self, payload: dict[str, Any]) -> bool:
        connection = self._connect()
        if connection is None:
            return False
        try:
            connection.sendall(
                (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            )
        except OSError as error:
            print(f"ROS中継への送信に失敗しました: {error}", file=sys.stderr)
            connection.close()
            self._socket = None
            return False
        return True

    def take_observed_actions(self) -> list[str]:
        """What the robot saw the participant do since the last call.

        They come with an utterance (``"observed_actions"`` of a
        ``user_input``), or on their own (``"type": "observed_actions"``)
        when the robot was busy and no turn was taken; either way they are
        to be recorded before the utterance, if any, is answered.
        """
        observed, self._observed = self._observed, []
        return observed

    def _keep_observed(self, payload: dict[str, Any]) -> None:
        actions = payload.get("observed_actions", [])
        if isinstance(actions, list):
            self._observed.extend(str(a).strip() for a in actions if str(a).strip())

    def receive_user_input(self, _prompt: str = "") -> str | None:
        """Return one relayed utterance, or ``None`` so timers (and observed
        actions, see ``take_observed_actions``) can be checked."""
        connection = self._connect()
        if connection is None:
            raise EOFError

        while True:
            while b"\n" in self._receive_buffer:
                raw_line, self._receive_buffer = self._receive_buffer.split(b"\n", 1)
                if not raw_line.strip():
                    continue
                try:
                    payload = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    print(f"ROS中継から不正な入力を受信しました: {error}", file=sys.stderr)
                    continue
                if not isinstance(payload, dict):
                    print("ROS中継からJSONオブジェクト以外を受信しました", file=sys.stderr)
                    continue
                if payload.get("type") == "observed_actions":
                    self._keep_observed(payload)
                    return None
                if payload.get("type") == "interaction_mode":
                    self.narrative_mode = bool(payload.get("narrative"))
                    if self.narrative_mode:
                        self.narrative_entries += 1
                    return None
                if payload.get("type") != "user_input":
                    continue
                self._keep_observed(payload)
                text = str(payload.get("text", "")).strip()
                if text:
                    return text

            try:
                readable, _, _ = select.select(
                    [connection], [], [], RECEIVE_POLL_SECONDS
                )
                if not readable:
                    return None
                chunk = connection.recv(4096)
            except OSError as error:
                print(f"ROS中継からの受信に失敗しました: {error}", file=sys.stderr)
                connection.close()
                self._socket = None
                raise EOFError from error
            if not chunk:
                connection.close()
                self._socket = None
                raise EOFError
            self._receive_buffer += chunk

