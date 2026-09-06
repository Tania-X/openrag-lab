# OpenRAG Lab Agent Instructions

These rules apply to any coding agent working in this repository.

## Git / PR Rules

- Do NOT push directly to `main`.
- Always develop on a `feat/*` branch and open a Pull Request.
- Never delete `feat/*` branches after merging.
- Merge Pull Requests with a regular merge commit, never squash.
- Follow the `pr-ai-review-loop` skill from the skills-hub repository:
  - https://github.com/Tania-X/skills-hub
  - skill path: `skills/pr-ai-review-loop/SKILL.md`
  - If AI Review reports severity >= 4, fix and push, then monitor again.
  - If severity < 4, stop and ask the human for a decision.

## Docs

- Project progress and pitfalls: `docs/progress-and-pitfalls.md`
