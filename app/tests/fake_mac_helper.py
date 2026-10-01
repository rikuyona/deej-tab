"""テスト用の偽の補助プログラム (mac/deej-tab-helper.swift と同じやりとりをする)

覚えた状態は命令 "state" で返す。"crash" で終了する (起動し直しのテスト用)
"""

import json
import sys

state = {"gains": {}, "others": None, "exclude": [], "output": 0.5, "input": 0.8,
         "meter": None, "hotkeys": {}}
PROCESSES = [{"name": "discord.app", "title": "Discord", "path": "/Applications/Discord.app", "output": True},
             {"name": "spotify.app", "title": "Spotify", "path": "/Applications/Spotify.app", "output": False}]


def handle(msg):
    cmd = msg["cmd"]
    if cmd == "hello":
        return {"version": 1}
    if cmd == "state":
        return {"state": state}
    if cmd == "crash":
        sys.exit(1)
    if cmd == "get_volume":
        return {"value": state[msg["scope"]]}
    if cmd == "set_volume":
        state[msg["scope"]] = msg["value"]
        return {}
    if cmd == "processes":
        return {"processes": PROCESSES}
    if cmd == "set_gains":
        state["gains"].update(msg["gains"])
        return {}
    if cmd == "set_others":
        state["others"], state["exclude"] = msg["gain"], msg["exclude"]
        state["gains"] = {k: v for k, v in state["gains"].items() if k in msg["exclude"]}
        return {}
    if cmd == "reset":
        state.update(gains={}, others=None, exclude=[])
        return {}
    if cmd == "meter":
        state["meter"] = {k: msg[k] for k in ("apps", "others_exclude", "master")}
        return {}
    if cmd == "peaks":
        return {"apps": {"discord.app": 0.5}, "others": 0.25, "master": 0.75}
    if cmd == "front":
        return {"name": "google chrome.app", "pid": 123, "full": False}
    if cmd == "apps":
        return {"apps": [{"name": "safari.app", "title": "Safari", "path": "/Applications/Safari.app"}]}
    if cmd == "names":
        return {"names": {p: p.rsplit("/", 1)[-1][:-4] for p in msg["paths"]}}
    if cmd == "icon":
        return {"png": "iVBORw0KGgo="}
    if cmd == "hotkeys":
        state["hotkeys"] = msg["bindings"]
        for name in msg["bindings"]:
            print(json.dumps({"event": "hotkey", "name": name}), flush=True)
        return {"errors": {}}
    raise ValueError(f"不明な命令です: {cmd}")


for line in sys.stdin:
    msg = json.loads(line)
    try:
        res = dict(handle(msg), id=msg["id"], ok=True)
    except Exception as e:
        res = {"id": msg["id"], "error": str(e)}
    print(json.dumps(res), flush=True)
