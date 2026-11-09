#!/usr/bin/env python3
"""Start the Web UI or the robot's chat from a menu instead of remembering options.

    python scripts/run.py

The first line chooses what to start: the Web UI (``web_server.py``) or the
robot (``pooh_narrative_dspy.py --ros-relay``, no Web UI). The menu starts
from ``config/run_defaults.json`` (shared, checked in; a section per
target); change only what differs this time (Enter on a line), then press s
to start. It offers only the options this version of each script has, shows
the command it will run, warns about what looks wrong (a port already in
use, a speech server or ROS relay that does not answer, no passphrase), and
asks once more before starting.

The passphrase is kept out of the shared defaults (the repository is public)
and remembered in ``.tools/run.json`` (not committed) instead.
"""

from __future__ import annotations

import argparse
import curses
import json
import locale
import os
import shlex
import socket
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "web": PROJECT_ROOT / "scripts" / "web_server.py",
    "robot": PROJECT_ROOT / "scripts" / "pooh_narrative_dspy.py",
}
TARGET_LABELS = {"web": "Web UI", "robot": "ロボット"}
DEFAULTS_PATH = PROJECT_ROOT / "config" / "run_defaults.json"
LOCAL_PATH = PROJECT_ROOT / ".tools" / "run.json"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

Settings = dict[str, Any]
InUse = Callable[[int, str], bool]

# pooh_body's pooh_operator.launch with use_narrative:=true starts the bridge
# and the chatbot itself; starting the robot from here as well would put two
# chatbots on one bridge.
ROBOT_NOTE = ("pooh_operator.launch を use_narrative:=true で起動しているなら、チャットボットは"
              "もう動いているので、ここでは起動しないでください（2つになります）。")


def load_settings(defaults_path: Path = DEFAULTS_PATH, local_path: Path = LOCAL_PATH) -> Settings:
    settings = json.loads(defaults_path.read_text(encoding="utf-8"))
    web = settings["web"]
    web["port"] = web.get("ports", {}).get(web["scenario"], 8080)
    try:
        local = json.loads(local_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        local = {}
    web["passphrase"] = local.get("passphrase", "") if isinstance(local, dict) else ""
    return settings


def save_local(settings: Settings, local_path: Path = LOCAL_PATH) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(json.dumps({"passphrase": settings["web"].get("passphrase", "")},
                                     ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def available_options() -> dict[str, set[str]]:
    """The option names (dests) each script of this version accepts."""
    import pooh_narrative_dspy
    import web_server

    return {
        "web": {action.dest for action in web_server.build_parser()._actions},
        "robot": {action.dest for action in pooh_narrative_dspy.build_parser()._actions},
    }


def crowdsourcing_scenarios() -> frozenset[str]:
    from crowdsourcing import SUPPORTED_SCENARIOS

    return SUPPORTED_SCENARIOS


def _flags(section: Settings, options: set[str], keys: tuple[str, ...]) -> list[str]:
    return ["--" + key.replace("_", "-") for key in keys if section.get(key) and key in options]


def build_web_argv(web: Settings, options: set[str], crowd_scenarios: frozenset[str]) -> list[str]:
    argv = ["--scenario", web["scenario"], "--port", str(web["port"])]
    argv += _flags(web, options, ("public", "storybook_generate", "postcards", "storybook_eink", "ros_relay"))
    if web.get("crowdsourcing") and web["scenario"] in crowd_scenarios:
        argv.append("--crowdsourcing")
        if web.get("passphrase", "").strip() and "passphrase" in options:
            argv += ["--passphrase", web["passphrase"].strip()]
        if "end_after_minutes" in options:
            argv += ["--end-after-minutes", str(web.get("end_after_minutes") or 0)]
    if web.get("tts") and web.get("tts_server_url"):
        argv += ["--tts-server-url", web["tts_server_url"]]
    return argv


def build_robot_argv(robot: Settings, options: set[str]) -> list[str]:
    argv = ["--scenario", robot["scenario"], "--ros-relay",
            "--ros-relay-host", str(robot["ros_relay_host"]),
            "--ros-relay-port", str(robot["ros_relay_port"])]
    argv += _flags(robot, options, ("start_on_mode", "no_idle_close", "storybook_eink",
                                    "storybook_generate", "postcards"))
    if robot.get("start_on_mode"):
        for extra in robot.get("extra_scenarios", []):
            if extra != robot["scenario"]:
                argv += ["--extra-scenario", extra]
    return argv


def build_command(settings: Settings, options: dict[str, set[str]],
                  crowd_scenarios: frozenset[str]) -> tuple[Path, list[str]]:
    """The script to run and its arguments, for the chosen target."""
    target = settings["target"]
    if target == "robot":
        return SCRIPTS["robot"], build_robot_argv(settings["robot"], options["robot"])
    return SCRIPTS["web"], build_web_argv(settings["web"], options["web"], crowd_scenarios)


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def running_robot_chatbots() -> list[int]:
    """PIDs of chatbots already talking to a robot (pooh_narrative_dspy.py --ros-relay)."""
    found = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(arg.endswith(b"pooh_narrative_dspy.py") for arg in args) and b"--ros-relay" in args:
            found.append(int(entry.name))
    return found


def warnings_for(settings: Settings, crowd_scenarios: frozenset[str],
                 in_use: InUse = port_in_use,
                 running: Callable[[], list[int]] = running_robot_chatbots) -> list[str]:
    found = []
    if settings["target"] == "robot":
        robot = settings["robot"]
        others = running()
        if others:
            found.append("ロボットにつながるチャットボットがもう動いています（PID "
                         + ", ".join(map(str, others)) + "）。pooh_operator.launch の use_narrative:=true "
                         "で起動していませんか。ここで起動すると2つになります。")
        if not in_use(int(robot["ros_relay_port"]), str(robot["ros_relay_host"])):
            found.append(f"ブリッジ narrative_motion_bridge.py（{robot['ros_relay_host']}:"
                         f"{robot['ros_relay_port']}）に接続できません。先に起動してください"
                         "（起動しなくても、つながるまで待ち続けます）。")
        return found
    web = settings["web"]
    if in_use(int(web["port"]), "127.0.0.1"):
        found.append(f"ポート {web['port']} はもう使われています（別のサーバが動いていませんか）。")
    if web.get("tts") and web.get("tts_server_url"):
        url = urlparse(web["tts_server_url"])
        if not url.hostname or not in_use(url.port or 80, url.hostname):
            found.append(f"音声合成サーバ（{web['tts_server_url']}）に接続できません。"
                         "起動しても音声なしになります。")
    if (web.get("crowdsourcing") and web["scenario"] in crowd_scenarios
            and not web.get("passphrase", "").strip()):
        found.append("合言葉が空です（完了コードの下に合言葉を出しません）。")
    return found


# --- the menu ---------------------------------------------------------------

def _item(key: str, label: str, value: Any, kind: str) -> dict:
    return {"key": key, "label": label, "value": value, "kind": kind}


def menu_items(settings: Settings, options: dict[str, set[str]], crowd_scenarios: frozenset[str],
               scenarios: list[str]) -> list[dict]:
    target = settings["target"]
    section = settings[target]
    on = lambda key: "[x]" if section.get(key) else "[ ]"
    items = [_item("target", "起動するもの", TARGET_LABELS[target], "target"),
             _item("scenario", "シナリオ", section["scenario"], "scenario")]
    if target == "robot":
        robot_options = options["robot"]
        if "start_on_mode" in robot_options:
            items.append(_item("start_on_mode", "物語の始め方",
                               "ナラティブモードに入るたびに始める"
                               if section.get("start_on_mode") else "起動してすぐ1回だけ", "flag"))
            if section.get("start_on_mode") and "extra_scenario" in robot_options:
                for other in scenarios:
                    if other != section["scenario"]:
                        checked = other in section.get("extra_scenarios", [])
                        items.append(_item("extra:" + other, f"  ほかに選べるシナリオ: {other}",
                                           "[x]" if checked else "[ ]", "extra"))
        for key, label in (("no_idle_close", "締めのあと黙っていても終えない"),
                           ("storybook_eink", "タブレットに絵本を書く"),
                           ("storybook_generate", "挿絵を生成する"),
                           ("postcards", "ポストカード")):
            if key in robot_options:
                items.append(_item(key, label, on(key), "flag"))
        host = str(section["ros_relay_host"])
        items.append(_item("ros_relay_host", "ブリッジ（narrative_motion_bridge.py）が動くPC",
                           host + ("（このPC）" if host in ("127.0.0.1", "localhost", "::1") else ""), "text"))
        items.append(_item("ros_relay_port", "ブリッジのポート", str(section["ros_relay_port"]), "number"))
    else:
        web_options = options["web"]
        items.append(_item("port", "ポート", str(section["port"]), "number"))
        items.append(_item("public", "公開する（--public）", on("public"), "flag"))
        if section["scenario"] in crowd_scenarios:
            items.append(_item("crowdsourcing", "クラウドソーシング", on("crowdsourcing"), "flag"))
            if section.get("crowdsourcing"):
                if "passphrase" in web_options:
                    items.append(_item("passphrase", "  合言葉", section.get("passphrase") or "（なし）", "text"))
                if "end_after_minutes" in web_options:
                    items.append(_item("end_after_minutes", "  終了ボタンを押せるまで（分）",
                                       str(section.get("end_after_minutes")), "number"))
        items.append(_item("storybook_generate", "挿絵を生成する", on("storybook_generate"), "flag"))
        items.append(_item("tts", "音声合成", on("tts"), "flag"))
        if section.get("tts"):
            items.append(_item("tts_server_url", "  音声合成サーバ", section.get("tts_server_url", ""), "text"))
        for key, label in (("postcards", "ポストカード"), ("storybook_eink", "タブレットに絵本を書く"),
                           ("ros_relay", "ロボットにも話させる（ROS中継へ送る）")):
            if key in web_options:
                items.append(_item(key, label, on(key), "flag"))
    items.append(_item("start", "▶ この設定で起動する（s）", "", "start"))
    return items


def _ask(stdscr: Any, prompt: str, default: str) -> str:
    """A plain input() prompt outside curses, so Japanese input works."""
    curses.def_prog_mode()
    curses.endwin()
    try:
        answer = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        answer = ""
    curses.reset_prog_mode()
    stdscr.refresh()
    return answer or default


def change(settings: Settings, item: dict, scenarios: list[str],
           ask: Callable[[str, str], str] = lambda prompt, default: default) -> None:
    key, kind = item["key"], item["kind"]
    section = settings[settings["target"]]
    if kind == "target":
        settings["target"] = "robot" if settings["target"] == "web" else "web"
    elif kind == "flag":
        section[key] = not section.get(key)
    elif kind == "extra":
        extras = section.setdefault("extra_scenarios", [])
        name = key.split(":", 1)[1]
        if name in extras:
            extras.remove(name)
        else:
            extras.append(name)
    elif kind == "scenario":
        current = scenarios.index(section["scenario"]) if section["scenario"] in scenarios else -1
        section["scenario"] = scenarios[(current + 1) % len(scenarios)]
        if "ports" in section:
            section["port"] = section["ports"].get(section["scenario"], section.get("port"))
    elif kind == "number":
        answer = ask(item["label"].strip(), str(section.get(key)))
        try:
            section[key] = float(answer) if key == "end_after_minutes" else int(answer)
        except ValueError:
            pass
    elif kind == "text":
        answer = ask(item["label"].strip() + "（空にするには - ）", str(section.get(key, "")))
        section[key] = "" if answer == "-" else answer


def run_menu(stdscr: Any, settings: Settings, options: dict[str, set[str]],
             crowd_scenarios: frozenset[str], scenarios: list[str]) -> bool:
    curses.curs_set(0)
    cursor = 0
    while True:
        items = menu_items(settings, options, crowd_scenarios, scenarios)
        cursor = min(cursor, len(items) - 1)
        stdscr.erase()
        stdscr.addstr(0, 0, "プーを起動します。↑↓で選び、Enter で変える。s で起動、q でやめる。")
        first = 2
        if settings["target"] == "robot":
            try:
                stdscr.addstr(1, 0, "注意: " + ROBOT_NOTE)
            except curses.error:
                pass
            first = 3
        for row, item in enumerate(items, start=first):
            line = f"{item['label']}  {item['value']}" if item["value"] else item["label"]
            attr = curses.A_REVERSE if row - first == cursor else curses.A_NORMAL
            try:
                stdscr.addstr(row, 2, line, attr)
            except curses.error:
                pass  # a terminal too small to show every line
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            cursor = (cursor - 1) % len(items)
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = (cursor + 1) % len(items)
        elif key in (ord("q"), 27):
            return False
        elif key == ord("s"):
            return True
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            if items[cursor]["kind"] == "start":
                return True
            change(settings, items[cursor], scenarios,
                   ask=lambda prompt, default: _ask(stdscr, prompt, default))


def main() -> int:
    parser = argparse.ArgumentParser(description="メニューから Web UI かロボットを起動する")
    parser.add_argument("--dry-run", action="store_true", help="既定の設定でのコマンドを表示するだけ")
    parser.add_argument("--target", choices=("web", "robot"), help="起動するものの既定を変える")
    args = parser.parse_args()
    from scenarios import SCENARIOS

    settings = load_settings()
    if args.target:
        settings["target"] = args.target
    options = available_options()
    crowd_scenarios = crowdsourcing_scenarios()
    if not args.dry_run:
        if not sys.stdin.isatty():
            print("端末から実行してください（--dry-run でコマンドだけ表示できます）。", file=sys.stderr)
            return 1
        locale.setlocale(locale.LC_ALL, "")
        if not curses.wrapper(run_menu, settings, options, crowd_scenarios, sorted(SCENARIOS)):
            print("起動しませんでした。")
            return 0
    script, argv = build_command(settings, options, crowd_scenarios)
    print(f"python {script.relative_to(PROJECT_ROOT)} " + " ".join(shlex.quote(a) for a in argv))
    if settings["target"] == "robot":
        print("注意: " + ROBOT_NOTE)
    for warning in warnings_for(settings, crowd_scenarios):
        print("注意: " + warning)
    if args.dry_run:
        return 0
    if input("これで起動しますか？ [Y/n]: ").strip().lower() not in ("", "y", "yes"):
        print("起動しませんでした。")
        return 0
    save_local(settings)
    os.execv(sys.executable, [sys.executable, str(script), *argv])


if __name__ == "__main__":
    raise SystemExit(main())
