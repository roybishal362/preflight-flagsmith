"""Static checks for the silent ways a Flagsmith experiment records nothing.

Each rule maps to a failure mode listed in the Flagsmith Experimentation docs
(set-up-with-a-coding-agent troubleshooting + run-an-experiment):
  PF001 events not enabled in the SDK      -> no exposures, no conversions
  PF002 experiment flag read without an identity -> no exposures
  PF003 tracked event name doesn't match a metric (case-sensitive) -> conversions ignored
  PF004 events buffered but never flushed in a short-lived process -> data lost on exit
  PF005 SDK too old for Experimentation    -> no experiment APIs / events
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
PY_EXT = {".py"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__", ".next", ".vercel"}

# Minimum SDK versions for Experimentation (docs: run-an-experiment, fetched 2026-11-21).
MIN_VERSIONS = {
    "@flagsmith/flagsmith": (12, 4, 0),
    "@flagsmith/nodejs": (9, 1, 0),
    "flagsmith": (6, 3, 0),  # Python
}
LEGACY_JS_PACKAGES = {"flagsmith-nodejs": "@flagsmith/nodejs >= 9.1.0"}


@dataclass
class Finding:
    rule: str
    severity: str  # "error" | "warning"
    file: str
    line: int
    message: str
    fix: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SourceFile:
    path: Path
    text: str
    lang: str  # "js" | "py"
    lines: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.lines = self.text.splitlines()
        pattern = JS_CONST if self.lang == "js" else PY_CONST
        self.consts = {m.group(1): m.group(2) for m in pattern.finditer(self.text)}
        self.code = code_only(self.text, self.lang)  # same length/lines as text; strings and comments blanked

    def line_of(self, index: int) -> int:
        return self.text.count("\n", 0, index) + 1


# ---- patterns ---------------------------------------------------------------
# An argument is either a string literal or a simple constant name (resolved per file).
_ARG = r"(?:['\"`]([^'\"`]+)['\"`]|([A-Za-z_$][\w$]*))"
JS_EXPERIMENT_READ = re.compile(r"\b(getExperimentFlag|useExperiment)\s*\(\s*" + _ARG)
JS_TRACK = re.compile(r"\btrackEvent\s*\(\s*" + _ARG)
JS_CONST = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*\w+\s*)?=\s*['\"`]([^'\"`]+)['\"`]")
PY_CONST = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::\s*\w+\s*)?=\s*['\"]([^'\"]+)['\"]", re.M)


def _resolve(f: "SourceFile", literal: str | None, name: str | None) -> str | None:
    if literal is not None:
        return literal
    consts = f.consts
    return consts.get(name) if name else None
JS_EVENTS_ON = re.compile(r"\benableEvents\s*:\s*true\b")
JS_IDENTIFY = re.compile(r"\bidentify\s*\(|\bidentity\s*:")
JS_NODE_CLIENT = re.compile(r"new\s+Flagsmith\s*\(")
JS_FLUSH = re.compile(r"\b(flushEvents|close)\s*\(")

PY_EXPERIMENT_READ = re.compile(r"\bget_experiment_flag\s*\(\s*['\"]([^'\"]+)['\"]\s*(\)|,)")
PY_EXPERIMENT_CALL = re.compile(r"\bget_experiment_flag\s*\(([^)]*)\)")
PY_TRACK = re.compile(r"\btrack_event\s*\(\s*(?:['\"]([^'\"]+)['\"]|([A-Za-z_]\w*))")
PY_CLIENT = re.compile(r"\bFlagsmith\s*\(")
PY_EVENTS_ON = re.compile(r"\benable_events\s*=\s*True\b")
PY_FLUSH = re.compile(r"\b(flush_events|close)\s*\(|max_buffer_items\s*=\s*1\b")
PY_SCRIPT = re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]")

# PF006: both SDKs send events to https://events.api.flagsmith.com/ by default, even when the flags API is
# pointed at a self-hosted instance (verified in @flagsmith/flagsmith 12.6 `eventsApiUrl` and Python
# flagsmith 6.7 `EventProcessorConfig(events_api_url=...)`).
JS_CUSTOM_API = re.compile(r"\bapi\s*:\s*(?!['\"`]?https://(?:edge\.)?api\.flagsmith\.com)[^,}\s][^,}\n]*")
JS_EVENTS_URL = re.compile(r"\beventsApiUrl\s*:")
PY_CUSTOM_API = re.compile(r"\bapi_url\s*=\s*(?!['\"]https://(edge\.)?api\.flagsmith\.com)[^,)\n]+")
PY_EVENTS_URL = re.compile(r"\bevents_api_url\s*=")


def code_only(text: str, lang: str) -> str:
    """Blank out comments and the contents of string literals (keeping quotes, length and newlines), so presence
    checks like "is identify() ever called?" are not fooled by `'… identify() …'` in UI text or comments."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        two = text[i:i + 2]
        three = text[i:i + 3]
        if lang == "js" and two == "//" or lang == "py" and ch == "#":
            j = text.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif lang == "js" and two == "/*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                out[k] = out[k] if text[k] == "\n" else " "
            i = j
        elif lang == "py" and three in ('"""', "'''"):
            j = text.find(three, i + 3)
            j = n if j == -1 else j + 3
            for k in range(i + 3, max(i + 3, j - 3)):
                out[k] = out[k] if text[k] == "\n" else " "
            i = j
        elif ch in "'\"" or (lang == "js" and ch == "`"):
            j = i + 1
            while j < n and text[j] != ch and not (text[j] == "\n" and ch != "`"):
                j += 2 if text[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                out[k] = out[k] if text[k] == "\n" else " "
            i = j + 1
        else:
            i += 1
    return "".join(out)



def _in_code(f: "SourceFile", m: re.Match) -> bool:
    """True if the match starts in real code, not inside a string literal or comment."""
    s = m.start()
    return f.code[s] == f.text[s] and not f.text[s].isspace()


def _find(rx: re.Pattern, f: "SourceFile") -> list[re.Match]:
    return [m for m in rx.finditer(f.text) if _in_code(f, m)]


def _search(rx: re.Pattern, f: "SourceFile") -> re.Match | None:
    found = _find(rx, f)
    return found[0] if found else None


def iter_sources(root: Path) -> list[SourceFile]:
    files: list[SourceFile] = []
    paths = [root] if root.is_file() else sorted(root.rglob("*"))
    for p in paths:
        if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
            continue
        ext = p.suffix.lower()
        lang = "js" if ext in JS_EXT else "py" if ext in PY_EXT else None
        if lang is None:
            continue
        try:
            files.append(SourceFile(p, p.read_text(encoding="utf-8", errors="replace"), lang))
        except OSError:
            continue
    return files


def _parse_version(spec: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", spec)
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def _fmt(v: tuple[int, ...]) -> str:
    return ".".join(str(x) for x in v)


def check_versions(root: Path) -> list[Finding]:
    out: list[Finding] = []
    base = root if root.is_dir() else root.parent
    for pkg_json in [p for p in base.rglob("package.json") if not any(s in p.parts for s in SKIP_DIRS)]:
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for name, spec in deps.items():
            if name in LEGACY_JS_PACKAGES:
                out.append(Finding("PF005", "error", str(pkg_json), 1,
                                   f"'{name}' cannot record experiment events.",
                                   f"Replace it with {LEGACY_JS_PACKAGES[name]}."))
            elif name in MIN_VERSIONS:
                v = _parse_version(spec)
                if v and v < MIN_VERSIONS[name]:
                    out.append(Finding("PF005", "error", str(pkg_json), 1,
                                       f"{name} {spec} is older than {_fmt(MIN_VERSIONS[name])}, the minimum for Experimentation.",
                                       f"npm install {name}@latest"))
    for req in [p for p in base.rglob("requirements*.txt") if not any(s in p.parts for s in SKIP_DIRS)]:
        for i, line in enumerate(req.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = re.match(r"\s*flagsmith\s*(==|>=|~=|<=)?\s*([\d.]+)?", line)
            if m and m.group(2):
                v = _parse_version(m.group(2))
                if v and v < MIN_VERSIONS["flagsmith"] and m.group(1) in ("==", "<=", "~="):
                    out.append(Finding("PF005", "error", str(req), i,
                                       f"flagsmith {m.group(2)} is older than 6.3.0, the minimum for Experimentation.",
                                       "pip install 'flagsmith>=6.3.0'"))
    return out


def lint(root: Path, metric_events: set[str] | None = None) -> list[Finding]:
    """Run all rules. metric_events: event names your Flagsmith metrics count (case-sensitive)."""
    files = iter_sources(root)
    findings: list[Finding] = []

    js_files = [f for f in files if f.lang == "js"]
    py_files = [f for f in files if f.lang == "py"]
    js_events_on = any(_search(JS_EVENTS_ON, f) for f in js_files)
    js_identify = any(_search(JS_IDENTIFY, f) for f in js_files)

    tracked: list[tuple[str, SourceFile, int]] = []

    for f in js_files:
        reads = list(_find(JS_EXPERIMENT_READ, f))
        tracks = list(_find(JS_TRACK, f))
        for m in tracks:
            name = _resolve(f, m.group(1), m.group(2))
            if name:
                tracked.append((name, f, f.line_of(m.start())))
        if (reads or tracks) and not js_events_on:
            first = (reads or tracks)[0]
            findings.append(Finding("PF001", "error", str(f.path), f.line_of(first.start()),
                                    "Experiment SDK calls found, but no `enableEvents: true` anywhere in the project. "
                                    "Flagsmith will record no exposures and no conversions.",
                                    "Pass `enableEvents: true` to flagsmith.init() / new Flagsmith()."))
        for m in reads:
            if not js_identify:
                flag = _resolve(f, m.group(2), m.group(3)) or m.group(3) or "?"
                findings.append(Finding("PF002", "error", str(f.path), f.line_of(m.start()),
                                        f"`{m.group(1)}('{flag}')` is called but the user is never identified. "
                                        "Exposures are only recorded for identified users.",
                                        "Call `await flagsmith.identify(<stable user or visitor id>)` before reading the flag."))
        custom = _search(JS_CUSTOM_API, f)
        if custom and _search(JS_EVENTS_ON, f) and not _search(JS_EVENTS_URL, f):
            findings.append(Finding("PF006", "error", str(f.path), f.line_of(custom.start()),
                                    "Flags come from a custom/self-hosted API, but events still go to Flagsmith Cloud "
                                    "(the SDK's events URL defaults to https://events.api.flagsmith.com/). "
                                    "Your instance will record no exposures or conversions.",
                                    "Also pass `eventsApiUrl: '<your host>/api/'` to init()."))
        if _search(JS_NODE_CLIENT, f) and _search(JS_EVENTS_ON, f) and not _search(JS_FLUSH, f):
            m = _search(JS_NODE_CLIENT, f)
            findings.append(Finding("PF004", "warning", str(f.path), f.line_of(m.start()),
                                    "Events are batched (every 10 s by default) but this file never flushes or closes the client. "
                                    "A short-lived process can exit before events are sent.",
                                    "Call `await flagsmith.close()` (Node) or `await flagsmith.flushEvents()` before exit."))

    for f in py_files:
        has_client = _search(PY_CLIENT, f)
        events_on = _search(PY_EVENTS_ON, f)
        for m in _find(PY_TRACK, f):
            name = _resolve(f, m.group(1), m.group(2))
            if name:
                tracked.append((name, f, f.line_of(m.start())))
        calls = list(_find(PY_EXPERIMENT_CALL, f))
        custom = _search(PY_CUSTOM_API, f)
        if has_client and custom and events_on and not _search(PY_EVENTS_URL, f):
            findings.append(Finding("PF006", "error", str(f.path), f.line_of(custom.start()),
                                    "Flags come from a custom/self-hosted API, but events still go to Flagsmith Cloud "
                                    "(EventProcessorConfig.events_api_url defaults to https://events.api.flagsmith.com/). "
                                    "Your instance will record no exposures or conversions.",
                                    "Pass event_processor_config=EventProcessorConfig(events_api_url='<your host>/api/')."))
        if has_client and (calls or _search(PY_TRACK, f)) and not events_on:
            findings.append(Finding("PF001", "error", str(f.path), f.line_of(has_client.start()),
                                    "Flagsmith client is created without `enable_events=True`. "
                                    "Flagsmith will record no exposures and no conversions.",
                                    "Flagsmith(environment_key=..., enable_events=True)"))
        for m in calls:
            args = m.group(1)
            if args.count(",") == 0 and "identifier" not in args:
                findings.append(Finding("PF002", "error", str(f.path), f.line_of(m.start()),
                                        "`get_experiment_flag()` is called without an identifier. Exposures need an identity.",
                                        "flagsmith.get_experiment_flag('<flag>', identifier=user_id)"))
        if has_client and events_on and _search(PY_SCRIPT, f) and not _search(PY_FLUSH, f):
            findings.append(Finding("PF004", "warning", str(f.path), f.line_of(has_client.start()),
                                    "This looks like a short-lived script with events enabled but no flush/close. "
                                    "Buffered events can be lost on exit.",
                                    "Call flagsmith.close() before exit, or use EventProcessorConfig(max_buffer_items=1)."))

    if metric_events is not None:
        folded = {e.casefold(): e for e in metric_events}
        for name, f, line in tracked:
            if name in metric_events:
                continue
            if name.casefold() in folded:
                findings.append(Finding("PF003", "error", str(f.path), line,
                                        f"Event '{name}' differs only in case from metric event '{folded[name.casefold()]}'. "
                                        "Event names are case-sensitive; Flagsmith silently ignores the mismatch.",
                                        f"Rename the tracked event to '{folded[name.casefold()]}'."))
            else:
                findings.append(Finding("PF003", "warning", str(f.path), line,
                                        f"Event '{name}' is tracked but no metric counts it, so it can't decide any experiment.",
                                        "Create a metric for it, or fix the name to match an existing metric."))
        tracked_names = {n for n, _, _ in tracked}
        for ev in sorted(metric_events - tracked_names):
            if ev.casefold() not in {t.casefold() for t in tracked_names}:
                findings.append(Finding("PF003", "error", str(root), 0,
                                        f"Metric event '{ev}' is never tracked anywhere in this code. Its experiment will get zero conversions.",
                                        f"Add trackEvent('{ev}') / track_event('{ev}', identifier=...) where success happens."))

    findings.extend(check_versions(root))
    findings.sort(key=lambda x: (x.severity != "error", x.file, x.line))
    return findings
