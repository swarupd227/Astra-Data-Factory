#!/usr/bin/env python3
"""WCAG 2.1 contrast checker for the workbench design system's own colour tokens (S6.0.4, ADR —
none; this is a UX artifact, not an agent). Computes real relative-luminance contrast ratios —
never eyeballed — and checks them against AA thresholds: 4.5:1 for normal text, 3:1 for large
text (>=18.66px bold or >=24px regular) and for UI component / focus-indicator boundaries
(WCAG 2.1 SC 1.4.11). Every pair below is a real token pair used in a real, published artifact
(docs/ux/design-system.md's tokens, and the S6.0.2 wireframe prototype's), not an invented sample.

Usage: python docs/ux/check_contrast.py
Exit 0: every pair meets its own threshold. Exit 1: at least one does not.
"""

from __future__ import annotations

import sys


def _linear(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# (label, foreground, background, threshold, role)
PAIRS = [
    # -- design-system.html, light theme --
    ("design-system / light — body text (ink on paper)", "#161b26", "#f7f8fa", 4.5, "text"),
    ("design-system / light — secondary text (ink-soft on paper-raised)", "#4c5567", "#ffffff", 4.5, "text"),
    ("design-system / light — faint labels (ink-faint on paper-raised)", "#5e6a80", "#ffffff", 4.5, "text"),
    ("design-system / light — link (accent-ink on paper-raised)", "#24379e", "#ffffff", 4.5, "text"),
    ("design-system / light — primary button (white on accent-fill)", "#ffffff", "#3454d1", 4.5, "text"),
    ("design-system / light — success chip text on chip bg", "#146942", "#e2f5ea", 4.5, "text"),
    ("design-system / light — warning chip text on chip bg", "#8f6300", "#fbf0d8", 4.5, "text"),
    ("design-system / light — critical chip text on chip bg", "#b3261e", "#fbe7e5", 4.5, "text"),
    ("design-system / light — info chip text on chip bg", "#226371", "#e2f2f4", 4.5, "text"),
    ("design-system / light — neutral chip text on chip bg", "#5b6472", "#eaecf0", 4.5, "text"),
    ("design-system / light — focus ring (focus on paper-raised)", "#3454d1", "#ffffff", 3.0, "ui"),
    # -- design-system.html, dark theme --
    ("design-system / dark — body text (ink on paper)", "#e9ecf3", "#0f131b", 4.5, "text"),
    ("design-system / dark — secondary text (ink-soft on paper-raised)", "#aab3c4", "#161c27", 4.5, "text"),
    ("design-system / dark — faint labels (ink-faint on paper-raised)", "#8894ac", "#161c27", 4.5, "text"),
    ("design-system / dark — link (accent-ink on paper-raised)", "#c4d2ff", "#161c27", 4.5, "text"),
    ("design-system / dark — primary button (white on accent-fill)", "#ffffff", "#3a58cc", 4.5, "text"),
    ("design-system / dark — success chip text on chip bg", "#5cd6a0", "#123326", 4.5, "text"),
    ("design-system / dark — warning chip text on chip bg", "#e7b955", "#392b0c", 4.5, "text"),
    ("design-system / dark — critical chip text on chip bg", "#f0847c", "#3a1917", 4.5, "text"),
    ("design-system / dark — info chip text on chip bg", "#7dd3e0", "#10313a", 4.5, "text"),
    ("design-system / dark — neutral chip text on chip bg", "#aab3c4", "#232b3a", 4.5, "text"),
    ("design-system / dark — focus ring (focus on paper-raised)", "#7f9bff", "#161c27", 3.0, "ui"),
    # -- wireframe prototype (S6.0.2), light theme --
    ("wireframes / light — body text (ink on paper)", "#22303c", "#eef1f4", 4.5, "text"),
    ("wireframes / light — secondary text (ink-soft on paper-raised)", "#5c6f7c", "#ffffff", 4.5, "text"),
    ("wireframes / light — faint labels (ink-faint on paper-raised)", "#667682", "#ffffff", 4.5, "text"),
    ("wireframes / light — active segmented control (white on accent-fill)", "#ffffff", "#2558d9", 4.5, "text"),
    ("wireframes / light — ok pill text on pill bg", "#256647", "#e2f1e9", 4.5, "text"),
    ("wireframes / light — warn pill text on pill bg", "#8a5c17", "#f7ecd8", 4.5, "text"),
    ("wireframes / light — danger pill text on pill bg", "#b13c3c", "#f9e6e6", 4.5, "text"),
    # -- wireframe prototype (S6.0.2), dark theme --
    ("wireframes / dark — body text (ink on paper)", "#e4ebf1", "#111a22", 4.5, "text"),
    ("wireframes / dark — secondary text (ink-soft on paper-raised)", "#9db0bd", "#182530", 4.5, "text"),
    ("wireframes / dark — faint labels (ink-faint on paper-raised)", "#8a9bac", "#182530", 4.5, "text"),
    ("wireframes / dark — active segmented control (white on accent-fill)", "#ffffff", "#3558c9", 4.5, "text"),
    ("wireframes / dark — ok pill text on pill bg", "#6fce9f", "#153327", 4.5, "text"),
    ("wireframes / dark — warn pill text on pill bg", "#e0ab5a", "#3a2c14", 4.5, "text"),
    ("wireframes / dark — danger pill text on pill bg", "#e07a7a", "#3a1f1f", 4.5, "text"),
]


def main() -> int:
    failed = 0
    rows = []
    for label, fg, bg, threshold, role in PAIRS:
        ratio = contrast_ratio(fg, bg)
        ok = ratio >= threshold
        rows.append((label, fg, bg, ratio, threshold, ok))
        if not ok:
            failed += 1
    width = max(len(r[0]) for r in rows)
    for label, fg, bg, ratio, threshold, ok in rows:
        status = "PASS" if ok else "FAIL"
        print(f"{label.ljust(width)}  {fg} on {bg}  {ratio:5.2f}:1  (needs {threshold:.1f}:1)  {status}")
    print()
    print(f"{len(rows) - failed}/{len(rows)} pairs meet AA; {failed} fail." if failed else f"All {len(rows)} pairs meet AA.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
