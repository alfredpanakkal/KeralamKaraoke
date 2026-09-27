# README Improvement Design — 2026-09-27

## Goal
Rewrite `README.md` so a non-coder can install and use the app in a 1-click flow,
with screenshots at each step. Method confirmed with user: `pip install` as the
manual/developer path; screenshots of karaoke progress bar and stems mode; fewer
words, step-by-step.

## Approved approach (A)
Lead with the 1-click launchers, demote manual pip to an "advanced" section,
technical detail under a `<details>` block, FAQ replaces scattered notes.

## Screenshots (user captures; README references)
Stored in `docs/images/`:

| File | What it shows | When to capture |
|---|---|---|
| `hero_karaoke_progress.png` | Progress bar + status + ETA | Karaoke mid-run |
| `karaoke_result.png` | Audio player + download button | Karaoke finished |
| `stems_result.png` | 4 stem players + download buttons | Stems finished |

Capture recipe: launch via `launch_karaoke.bat`, upload a short song (<30 s),
`Win+Shift+S`, save into `docs/images/`.

## Structure
1. Title + one-line description + hero image
2. Easy install (4 steps: download ZIP, extract, double-click launcher, wait)
   - collapsed NVIDIA GPU speed box (venv-aware pip command)
3. How to use — 3 steps, images inline, labels from current app.py
   ("Generate" button, mode radio, model dropdown, pitch slider)
4. FAQ — 3 items (slow/NVIDIA, output locations, 30-min cap)
5. `<details>` Technical details — existing "How it works", output layout, notes
6. Manual install (developers) — venv + `pip install -r requirements.txt`
7. Tests — one-liner

## Fixes applied while planning
- Button label is "Generate" (app.py:329), not "Generate Karaoke Track" (PLAN.MD
  is historical — not a doc source).
- No `pip install karaoke` claim — no package exists; manual path is
  `pip install -r requirements.txt`.
- Launcher path gives CPU-only torch; GPU box must use
  `karaoke-env\Scripts\pip` so it lands in the launcher's venv.
- Images live in `docs/images/`, not repo root.
- First-run cost stated once in step 3, not repeated in notes/FAQ.
