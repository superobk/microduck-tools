#!/usr/bin/env python3
"""Presentation-only QA against the old loopback DEMO. Never submits a form."""
import argparse, json, subprocess
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--session", required=True)
p.add_argument("--out", required=True, type=Path)
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=True)
root = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(
        ["agent-browser", "--session", a.session, *args],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    ).stdout.strip()


def ev(js):
    return json.loads(run("eval", js))


assert ev("location.hostname") == "127.0.0.1"
assert ev('fetch("/api/status").then(r=>r.json()).then(s=>s.demo)') is True
css = (root / "studio/static/calibration-mobile.css").read_text()
ev(
    '(()=>{const style=document.createElement("style");style.textContent='
    + json.dumps(css)
    + ";document.head.append(style);return true})()"
)
ev(
    "(()=>{"
    + (root / "studio/static/calibration-mobile.js").read_text()
    + ";return true})()"
)
results = []
for width in (1440, 768, 390):
    run("set", "viewport", str(width), "1000")
    for tab in ("overview", "imu", "camera", "joints", "servo", "preview", "guide"):
        run("click", "[data-tab=" + tab + "]")
        run("wait", "100")
        r = ev(
            '({width:innerWidth,tab:document.querySelector(".tab.active").id,overflow:document.documentElement.scrollWidth>innerWidth})'
        )
        assert not r["overflow"]
        results.append(r)
        if width == 390 and tab in ("joints", "imu", "camera"):
            run("screenshot", str(a.out / ("legacy-" + tab + "-mobile.png")))
(a.out / "legacy-mobile-qa.json").write_text(
    json.dumps(
        dict(
            layouts=results,
            form_submissions=0,
            source="legacy DEMO; injected identical gateway CSS/labels",
            hardware="NOT_RUN",
        ),
        ensure_ascii=False,
        indent=2,
    )
)
print("PASS: 21 legacy page/size combinations; no form submission")
