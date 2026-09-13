# Usability test and accessibility check — status

Story S6.0.4 (E6, F6.0, WBS 2.6.12). This story's two acceptance criteria are not the same kind
of thing. One needs real ops users during a real pilot; the other is a real, checkable technical
property of real artifacts. They are reported separately and honestly below — neither is claimed
done on the other's behalf.

## AC1 — usability session: not done, needs the pilot

"Five tasks completed by three users; issues ranked and fixed before G1" needs an actual pilot
with actual ops users — nothing this repository can substitute for. It has not happened. When it
does, S6.0.2's [wireframe prototype](wireframes.md) already has a working findings log (the "Log
a finding" drawer, backed by a shared database) ready to capture it, and this file is where the
ranked issues and their fixes should be recorded once real.

## AC2 — keyboard navigation and contrast: checked against what exists today

"On all P1 screens" is the catch: most P1 screens (S6.3.1–S6.3.13, S6.2.1–S6.2.5) are backlog
stories, not built screens, so there is nothing yet to run a per-screen pass against. What
*does* exist and *is* checkable today is the two real, published, interactive artifacts this
project has actually built — the [S6.0.2 wireframe prototype](wireframes.md) and the
[S6.0.3 design system](design-system.md) — plus the colour token system every future P1 screen
would inherit from the design system. Auditing the tokens now, before screens are built from
them, is the highest-leverage check available; each screen still needs its own pass once built,
noted in [Known gaps](#known-gaps).

### Contrast — real numbers, not eyeballed

`docs/ux/check_contrast.py` computes WCAG 2.1 relative-luminance contrast ratios (never
estimated) for every colour-token pair actually used in the two artifacts, in both light and dark
theme, and checks each against its real AA threshold — 4.5:1 for text, 3:1 for UI-component and
focus-indicator boundaries (SC 1.4.11).

```bash
python docs/ux/check_contrast.py
```

**First run found 10 real failures**, not zero — this was a genuine audit, not a formality:

| Token | Where | Before | After |
|---|---|---|---|
| `--ink-faint` | design-system, light | 3.65:1 | 5.46:1 |
| `--success` | design-system, light | 4.41:1 | 5.91:1 |
| `--info` | design-system, light | 4.28:1 | 5.90:1 |
| `--ink-faint` | design-system, dark | 4.44:1 | 5.59:1 |
| white on `--accent` (primary button) | design-system, dark | 2.61:1 | — |
| `--ink-faint` | wireframes, light | 2.89:1 | 4.69:1 |
| `--ok` | wireframes, light | 4.47:1 | 5.86:1 |
| `--warn` | wireframes, light | 3.56:1 | 4.95:1 |
| white on `--accent` (active control) | wireframes, dark | 2.68:1 | — |
| `--ink-faint` | wireframes, dark | 3.97:1 | 5.47:1 |

Two of these (white text on a filled accent background, in dark mode, in both artifacts) needed
more than a darker shade of the same token: `--accent` in dark mode is deliberately light so it
reads as text/icon/border color against a dark background, and that same lightness is exactly
what makes it fail behind white button text. The fix is a second token, `--accent-fill`, used
only for filled surfaces that hold white text — equal to `--accent` in light mode (already
passing), independently darkened in dark mode (design-system: `#3a58cc`, 6.09:1; wireframes:
`#3558c9`, 6.19:1). Every other failure was a straightforward darken (light theme) or lighten
(dark theme) of the same token, re-verified by the same script, not by eye.

**Current result: all 36 checked pairs meet AA**, confirmed by running the script — exit 0.

### Keyboard navigation — a static audit of both artifacts

Checked directly against the shipped markup, not assumed:

- Every interactive control in both artifacts is a native, keyboard-operable HTML element —
  `<button>`, `<a href>`, `<input>`, `<select>`, `<textarea>`. Zero `onclick` handlers on a
  `<div>` or `<span>`, zero `role="button"` standing in for a real button.
- Zero positive `tabindex` in either file — tab order follows document order throughout, the
  only order that stays correct as content changes.
- `:focus-visible` is styled globally in both artifacts with a visible outline — confirmed
  present exactly once per file, applied to every focusable element, not per-component.
- The wireframe prototype's findings drawer moves focus to its first field on open and closes on
  `Escape`, so a keyboard user is never dropped into an off-screen or unreachable state.

One gap found and fixed in passing (not itself part of AC2, but the same pass surfaced it): the
design system's status-chip icons are inline SVGs next to their own text label — without
`aria-hidden="true"` a screen reader could announce the icon redundantly or unhelpfully before
the label. All 25 instances (plus the one generated at runtime by the form-validation demo) now
carry it.

## Known gaps

- **Every future P1 screen still needs its own keyboard and contrast pass once built** — this
  check covers the token system and the two artifacts that exist today, not screens that don't
  exist yet. Re-running `check_contrast.py` against a new screen's actual rendered colours (not
  just the shared tokens, in case a screen introduces a one-off colour) should be part of that
  screen's own definition of done.
- **Screen-reader behavior beyond the one `aria-hidden` pass** was not audited — AC2 names
  keyboard navigation and contrast specifically; a full screen-reader pass (landmark structure,
  live-region announcements for the findings drawer, table semantics) is a separate, larger check
  this story's own AC does not ask for, listed here so it is not mistaken for already covered.
- **`check_contrast.py` checks the tokens, not rendered pixels** — a component that sets an
  inline colour outside the token system (none do today, verified by the greps above) would not
  be caught; worth a lint rule once real screen code exists.

## Validation status

| Item | Status |
|---|---|
| Keyboard navigation audited (existing artifacts) | Done — see above |
| Contrast checked and passing (existing artifacts) | Done — `docs/ux/check_contrast.py` exits 0 |
| Keyboard/contrast pass on every P1 screen | **Not done** — most P1 screens are not built yet |
| Five tasks completed by three real users | **Not held** |
| Issues ranked and fixed before G1 | **Not done** — no session has happened to produce issues |

S6.0.4 stays open until the pilot happens and every future P1 screen gets its own pass.
