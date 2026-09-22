"""TCP client for the optional narrative ROS relay."""

from __future__ import annotations

import json
import socket
import sys
from typing import Any


class NarrativeRelayPublisher:
    """Send narrative turns to a separate ROS relay over localhost TCP."""

    def __init__(self, host: str, port: int, scenario_key: str) -> None:
        self.host = host
        self.port = port
        self.scenario_key = scenario_key
        self._socket: socket.socket | None = None

    def _connect(self) -> socket.socket | None:
        if self._socket is not None:
            return self._socket
        try:
            self._socket = socket.create_connection((self.host, self.port), timeout=2.0)
        except OSError as error:
            print(
                f"ROS中継({self.host}:{self.port})に接続できません: {error}",
                file=sys.stderr,
            )
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
    ) -> None:
        payload: dict[str, Any] = {
            "scenario": self.scenario_key,
            "bot_response": bot_response,
            "narrative_actions": narrative_actions,
            "scene_id": scene_id,
            "source": source,
        }
        if world_event_id is not None:
            payload["world_event_id"] = world_event_id
        connection = self._connect()
        if connection is None:
            return
        try:
            connection.sendall(
                (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            )
        except OSError as error:
            print(f"ROS中継への送信に失敗しました: {error}", file=sys.stderr)
            connection.close()
            self._socket = None
