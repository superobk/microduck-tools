#!/usr/bin/env python3
"""Local browser QA after pairing DEMO. Refuses live mode; never accesses a real robot."""
import argparse
import json
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--session", required=True)
p.add_argument("--out", type=Path, required=True)
args = p.parse_args()
args.out.mkdir(exist_ok=True, parents=True)
transcript = []


def command(*argv):
    r = subprocess.run(
        ["agent-browser", "--session", args.session, *argv],
        capture_output=True,
        text=True,
        timeout=45,
        check=True,
    )
    transcript.append(dict(command=list(argv), result=r.stdout.strip()))
    return r.stdout.strip()


def evaluate(js):
    return json.loads(command("eval", js))


if evaluate("window.studioDebug.mode") != "simulation":
    raise SystemExit("QA refuses LIVE mode")
command("find", "role", "button", "click", "--name", "◉ 总览")
themes = []
for theme in ("garden", "harbor", "forest", "candy", "snow", "workshop", "space"):
    command("select", "#theme", theme)
    command("wait", "2300")
    d = evaluate("window.studioDebug")
    themes.append(d)
    if (
        d["render"].get("calls", 999) > 45
        or d["render"].get("triangles", 99999) > 15000
    ):
        raise RuntimeError("render budget exceeded or WebGL unavailable")
    command("screenshot", str(args.out / ("scene-" + theme + ".png")))
command("set", "viewport", "1440", "1000")
command("select", "#theme", "garden")
command("wait", "1000")
command("screenshot", str(args.out / "desktop-overview.png"))
layouts = []
for width in (1440, 768, 390):
    command("set", "viewport", str(width), "1000")
    for name in ("◉ 总览", "⌘ 控制", "▤ 监控", "◇ Policy", "⌁ 手柄"):
        command("find", "role", "button", "click", "--name", name)
        d = evaluate(
            "({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,tab:document.body.dataset.tab})"
        )
        layouts.append(d)
        if d["overflow"]:
            raise RuntimeError("horizontal overflow")
    command("screenshot", str(args.out / ("pad-" + str(width) + ".png")))
command("find", "role", "button", "click", "--name", "⌘ 控制")
command("select", "#inputSource", "keyboard")
command("click", "#world")
before = evaluate('document.getElementById("position").textContent')
evaluate(
    'document.getElementById("world").dispatchEvent(new KeyboardEvent("keydown",{code:"Space",key:" ",bubbles:true}));document.getElementById("world").dispatchEvent(new KeyboardEvent("keydown",{code:"KeyW",key:"w",bubbles:true}));true'
)
command("wait", "650")
evaluate(
    'document.getElementById("world").dispatchEvent(new KeyboardEvent("keyup",{code:"KeyW",key:"w",bubbles:true}));document.getElementById("world").dispatchEvent(new KeyboardEvent("keyup",{code:"Space",key:" ",bubbles:true}));true'
)
command("wait", "250")
after = evaluate('document.getElementById("position").textContent')
if before == after:
    raise RuntimeError("keyboard did not move simulated feedback")
command("select", "#simFault", "imu")
command("find", "role", "button", "click", "--name", "▤ 监控")
command("wait", "800")
diagnostic = evaluate('document.getElementById("diagnostics").textContent')
assert "IMU_FROZEN" in diagnostic and "恢复验证" in diagnostic
command("screenshot", str(args.out / "mobile-diagnostics.png"))
command("select", "#simFault", "none")
# Synthetic Gamepad API fixture: only installed after checking simulation mode.
command("find", "role", "button", "click", "--name", "⌘ 控制")
command("select", "#inputSource", "gamepad")
evaluate(
    'window.qaPad={id:"SYNTHETIC QA GAMEPAD",index:0,mapping:"standard",axes:[0,0,0,0],buttons:Array.from({length:16},()=>({pressed:false,value:0}))};Object.defineProperty(navigator,"getGamepads",{configurable:true,value:()=>[window.qaPad]});true'
)
command("wait", "150")
before = evaluate('document.getElementById("position").textContent')
evaluate("qaPad.axes[1]=-1;qaPad.buttons[4].pressed=true;true")
command("wait", "650")
evaluate("qaPad.axes[1]=0;qaPad.buttons[4].pressed=false;true")
command("wait", "250")
after = evaluate('document.getElementById("position").textContent')
assert before != after
command("screenshot", str(args.out / "mobile-control.png"))
evaluate("delete navigator.getGamepads;true")
(args.out / "browser-qa.json").write_text(
    json.dumps(
        dict(
            themes=themes,
            layouts=layouts,
            injected_keyboard_simulation="PASS",
            injected_gamepad_simulation="PASS",
            diagnostics="PASS",
            real_devices="NOT_RUN",
            hardware="NOT_RUN",
        ),
        ensure_ascii=False,
        indent=2,
    )
)
(args.out / "browser-transcript.json").write_text(
    json.dumps(transcript, ensure_ascii=False, indent=2)
)
print(
    "PASS: 7 scenes, 15 layouts, keyboard + injected gamepad, detailed diagnostic; real devices NOT_RUN"
)
