# OpenRAG Lab Agent Instructions

These rules apply to any coding agent working in this repository.

## Git / PR Rules

- Do NOT push directly to `main`.
- Always develop on a `feat/*` branch and open a Pull Request.
- Never delete `feat/*` branches after merging.
- Merge Pull Requests with a regular merge commit, never squash.
- Follow the `ai-review-loop` collaboration skill:
  - If AI Review reports severity >= 4, fix and push, then monitor again.
  - If severity < 4, stop and ask the human for a decision.

## Memory / Docs

- Persistent assistant memory: `/Users/apple/dsh/.ai-memory.md`
- Collaboration skill: `/Users/apple/dsh/skills/ai-review-loop.md`
