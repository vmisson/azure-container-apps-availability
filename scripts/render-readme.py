#!/usr/bin/env python3
"""Generate the static "dashboard" block injected into the README.

From the JSON produced by build-dashboard-data.py, this script writes:
  - assets/history-light.svg / assets/history-dark.svg : history chart
    (two versions to adapt to GitHub's light/dark theme);
  - README.md : replaces the content between the markers
    <!-- DASHBOARD:START --> and <!-- DASHBOARD:END -->.

The README rendered by GitHub supports neither JavaScript nor CSS: everything
is therefore generated as Markdown + static SVG images, regenerated on each
GitHub Action run and then committed to the repository.

Environment variables:
  DATA_DIR     directory of the JSON files (default: dashboard/data)
  README_PATH  path to the README to update (default: README.md)
  ASSETS_DIR   directory of the generated SVGs (default: assets)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import quote

START_MARKER = "<!-- DASHBOARD:START -->"
END_MARKER = "<!-- DASHBOARD:END -->"

STATUS_META = {
    "OK": {"label": "Available", "color": "#22c55e", "dot": "\U0001F7E2"},
    "CAPACITY": {"label": "Saturated", "color": "#f59e0b", "dot": "\U0001F7E0"},
    "ERROR": {"label": "Error", "color": "#ef4444", "dot": "\U0001F534"},
    "TIMEOUT": {"label": "Timeout", "color": "#a855f7", "dot": "\U0001F7E3"},
}
STATUS_ORDER = ["OK", "CAPACITY", "ERROR", "TIMEOUT"]
PROBLEM_ORDER = ["CAPACITY", "ERROR", "TIMEOUT"]

THEMES = {
    "light": {
        "bg": "#ffffff",
        "border": "#dce3f1",
        "grid": "#eef2f7",
        "text": "#5a688a",
        "axis": "#14203a",
    },
    "dark": {
        "bg": "#0d1117",
        "border": "#2c3a5e",
        "grid": "#21304f",
        "text": "#9fb0d4",
        "axis": "#e6ecff",
    },
}


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return default


def fmt_utc(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return iso or "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def fmt_short(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return ""
    return dt.strftime("%d/%m %H:%M")


def esc_xml(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def esc_cell(value: str) -> str:
    """Escape a value for a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def build_svg(history: list[dict], theme: dict) -> str:
    W, H = 960, 320
    pad = {"t": 52, "r": 22, "b": 34, "l": 42}
    iw = W - pad["l"] - pad["r"]
    ih = H - pad["t"] - pad["b"]

    def frame(inner: str) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'width="{W}" height="{H}" font-family="Segoe UI, system-ui, -apple-system, '
            f'Roboto, Helvetica, Arial, sans-serif" role="img" '
            f'aria-label="Availability history per region">'
            f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="14" '
            f'fill="{theme["bg"]}" stroke="{theme["border"]}"/>'
            f"{inner}</svg>"
        )

    if not history:
        msg = (
            f'<text x="{W / 2}" y="{H / 2}" text-anchor="middle" font-size="15" '
            f'fill="{theme["text"]}">No history yet: it grows with every run.</text>'
        )
        return frame(msg)

    n = len(history)
    series = [k for k in ("OK", "CAPACITY", "ERROR", "TIMEOUT")
              if any((h.get(k) or 0) > 0 for h in history)]
    if not series:
        series = ["OK"]

    max_y = max([1] + [h.get("total") or 0 for h in history])

    def x_at(i: int) -> float:
        return pad["l"] + (iw / 2 if n == 1 else (i / (n - 1)) * iw)

    def y_at(v: float) -> float:
        return pad["t"] + ih - (v / max_y) * ih

    # Horizontal grid lines + Y ticks.
    grid = []
    for frac in (0, 0.25, 0.5, 0.75, 1):
        y = pad["t"] + ih - frac * ih
        val = round(frac * max_y)
        grid.append(
            f'<line x1="{pad["l"]}" y1="{y:.1f}" x2="{W - pad["r"]}" y2="{y:.1f}" '
            f'stroke="{theme["grid"]}" stroke-width="1"/>'
            f'<text x="{pad["l"] - 7}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="11" fill="{theme["text"]}">{val}</text>'
        )

    # Per-status curves.
    paths = []
    for key in series:
        color = STATUS_META[key]["color"]
        pts = " ".join(
            f'{x_at(i):.1f},{y_at(h.get(key) or 0):.1f}' for i, h in enumerate(history)
        )
        dots = ""
        if n <= 60:
            dots = "".join(
                f'<circle cx="{x_at(i):.1f}" cy="{y_at(h.get(key) or 0):.1f}" '
                f'r="2.5" fill="{color}"/>'
                for i, h in enumerate(history)
            )
        paths.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="2.5" '
            f'stroke-linejoin="round" stroke-linecap="round" points="{pts}"/>{dots}'
        )

    # X-axis labels (first / last point).
    x_labels = (
        f'<text x="{pad["l"]}" y="{H - 12}" font-size="11" '
        f'fill="{theme["text"]}">{esc_xml(fmt_short(history[0].get("timestamp", "")))}</text>'
        f'<text x="{W - pad["r"]}" y="{H - 12}" text-anchor="end" font-size="11" '
        f'fill="{theme["text"]}">{esc_xml(fmt_short(history[-1].get("timestamp", "")))}</text>'
    )

    # Title + legend at the top.
    title = (
        f'<text x="{pad["l"]}" y="26" font-size="14" font-weight="600" '
        f'fill="{theme["axis"]}">History · {n} point{"s" if n > 1 else ""}</text>'
    )
    legend_items = []
    lx = pad["l"]
    for key in series:
        meta = STATUS_META[key]
        legend_items.append(
            f'<circle cx="{lx + 5}" cy="40" r="5" fill="{meta["color"]}"/>'
            f'<text x="{lx + 15}" y="44" font-size="12" fill="{theme["text"]}">'
            f'{esc_xml(meta["label"])}</text>'
        )
        lx += 28 + len(meta["label"]) * 7.2
    legend = "".join(legend_items)

    return frame(title + legend + "".join(grid) + "".join(paths) + x_labels)


def build_badges(summary: dict) -> str:
    badges = []
    for key in STATUS_ORDER:
        meta = STATUS_META[key]
        label = quote(meta["label"])
        value = summary.get(key, 0)
        color = meta["color"].lstrip("#")
        badges.append(
            f"![{meta['label']}](https://img.shields.io/badge/{label}-{value}-{color}"
            f"?style=flat-square)"
        )
    total = summary.get("total", 0)
    badges.append(
        f"![Total](https://img.shields.io/badge/Total%20tested-{total}-4f8cff"
        f"?style=flat-square)"
    )
    return " ".join(badges)


def build_problem_table(regions: list[dict]) -> str:
    rows = [r for r in regions if r.get("status") in PROBLEM_ORDER]
    rows.sort(key=lambda r: (PROBLEM_ORDER.index(r["status"]), r.get("region", "")))
    if not rows:
        return "> ✅ **All tested regions are available.**"
    lines = ["| Status | Region | Detail |", "| :--- | :--- | :--- |"]
    for r in rows:
        meta = STATUS_META.get(r["status"], {"label": r["status"], "dot": ""})
        lines.append(
            f"| {meta['dot']} {meta['label']} | `{esc_cell(r.get('region', ''))}` "
            f"| {esc_cell(r.get('detail', ''))} |"
        )
    return "\n".join(lines)


def build_ok_details(regions: list[dict]) -> str:
    ok = sorted(r.get("region", "") for r in regions if r.get("status") == "OK")
    if not ok:
        return ""
    chips = ", ".join(f"`{esc_cell(name)}`" for name in ok)
    return (
        f"<details>\n<summary>\U0001F7E2 {len(ok)} available regions</summary>\n\n"
        f"{chips}\n\n</details>"
    )


def render_block(latest: dict, history: list[dict]) -> str:
    summary = latest.get("summary", {})
    regions = latest.get("regions", [])
    generated = fmt_utc(latest.get("generated_at", ""))

    parts = [
        "## \U0001F30D Azure Container Apps availability",
        "",
        f"> Capacity to create a Container App Environment per region · "
        f"automatically updated on **{generated}**.",
        "",
        build_badges(summary),
        "",
        "<picture>",
        '  <source media="(prefers-color-scheme: dark)" '
        'srcset="assets/history-dark.svg" />',
        '  <img alt="Availability history per region" '
        'src="assets/history-light.svg" width="900" />',
        "</picture>",
        "",
        "### Regions to watch",
        "",
        build_problem_table(regions),
        "",
        build_ok_details(regions),
        "",
        f"<sub>Updated hourly via GitHub Actions · "
        f"<a href=\"#interactive-dashboard\">interactive version</a> on GitHub Pages.</sub>",
    ]
    return "\n".join(p for p in parts if p is not None)


def inject(readme_path: str, block: str) -> None:
    section = f"{START_MARKER}\n{block}\n{END_MARKER}"
    if os.path.exists(readme_path):
        with open(readme_path, encoding="utf-8") as handle:
            content = handle.read()
    else:
        content = f"# Azure Container Apps – availability per region\n\n{START_MARKER}\n{END_MARKER}\n"

    start = content.find(START_MARKER)
    end = content.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        content = content[:start] + section + content[end + len(END_MARKER):]
    else:
        content = content.rstrip() + "\n\n" + section + "\n"

    with open(readme_path, "w", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    data_dir = os.environ.get("DATA_DIR", "dashboard/data")
    readme_path = os.environ.get("README_PATH", "README.md")
    assets_dir = os.environ.get("ASSETS_DIR", "assets")

    latest = load_json(os.path.join(data_dir, "latest.json"), {})
    history = load_json(os.path.join(data_dir, "history.json"), [])
    if not isinstance(history, list):
        history = []

    os.makedirs(assets_dir, exist_ok=True)
    for name, theme in THEMES.items():
        with open(os.path.join(assets_dir, f"history-{name}.svg"), "w", encoding="utf-8") as handle:
            handle.write(build_svg(history, theme))

    inject(readme_path, render_block(latest, history))

    summary = latest.get("summary", {})
    print(f"README updated ({readme_path}) + SVGs written to {assets_dir}/.")
    print(f"Summary: {summary}")


if __name__ == "__main__":
    main()
