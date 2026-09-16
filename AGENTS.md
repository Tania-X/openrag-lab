# OpenRAG Lab Agent Instructions

These rules apply to any coding agent working in this repository.

## Git / PR Rules

- Do NOT push directly to `main`. Work on a `feat/*` branch and open a Pull Request.
- Never delete `feat/*` branches after merging.
- Merge Pull Requests with a regular merge commit, never squash.
- Follow the `pr-ai-review-loop` skill (skills-hub, `skills/pr-ai-review-loop/SKILL.md`):
  severity >= 4 → fix, push, monitor again; below that → stop and ask the human.
- Branch discipline: **check the current branch before every git command,
  and again before commit or push.**

### Few-shots (why that rule exists)

| Incident | Lesson |
|---|---|
| `--amend` landed on `main` — a `checkout` failed silently mid-chain | Re-check the branch after any `checkout` in the same chain |
| `push origin <branch>` "succeeded" but pushed nothing | It pushes the local ref, not HEAD — confirm both match |
| stash popped on the wrong branch, stale entry left behind | Check the branch first; `git stash list` afterwards |
| conflicted PR → no CI, no AI Review | Rebase onto `origin/main` first; then confirm `mergeable` |
| edits on a stale `main` → conflict + duplicate changes | `git fetch` and read `origin/main` before editing |
| `--force-with-lease <url>` → `stale info` | Pass an explicit `<branch>:<sha>` expectation |
| token left in `.git/config` | `git remote set-url` back after pushing |
| temp / merge-sim branches leaked into other work | Read `status` + `diff --stat` before committing; delete them |

## Docs

- Project progress and pitfalls: `docs/progress-and-pitfalls.md`
