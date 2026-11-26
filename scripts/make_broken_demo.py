"""Create demo_broken/: a copy of the demo page with three realistic, silent experiment bugs, for the video.

  1. enableEvents removed         -> PF001 (no exposures, no conversions)
  2. identify() removed           -> PF002 (no exposures)
  3. event name with wrong case   -> PF003 (conversions silently ignored)
"""
from pathlib import Path

src = Path(__file__).resolve().parents[1] / "web" / "src" / "App.tsx"
out = Path(__file__).resolve().parents[1] / "demo_broken"
out.mkdir(exist_ok=True)
text = src.read_text(encoding="utf-8")
for old, new in [
    ("enableEvents: true, ", ""),
    ("      await flagsmith.identify(visitorId())\n", ""),
    ("'demo_button_clicked'", "'Demo_Button_Clicked'"),
]:
    assert old in text, f"expected to find {old!r} in App.tsx"
    text = text.replace(old, new, 1)
(out / "App.tsx").write_text(text, encoding="utf-8")
print(f"wrote {out / 'App.tsx'}")
