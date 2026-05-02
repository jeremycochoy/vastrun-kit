# CLAUDE.md

## Design philosophy

The tool should be so well-designed that agents (and humans) fall into the right path naturally. Failures are unlikely, and when they happen, recovery is self-evident.

## GitHub workflow

- **Feature branches only.** All work goes on a feature branch. Never commit directly to `main` or to the active integration branch.
- **TDD, strict.** Red → green → refactor. Write a failing test first, implement until it passes, then clean up. Every PR carries tests for the behaviour it changes.
- **One PR per concern.** Keep PRs focused on a single issue. Bundle related cleanup with the change that motivates it; otherwise split.
- **Worktrees, not the main checkout.** Other agents may be using the main tree concurrently. Always `git worktree add /tmp/vastrun-kit-<feature> -b feat/<name>` and remove the worktree when done.
- **Post-merge.** After closing an issue, review the merged code and open at least one follow-up issue for anything worth tracking.

## Agent attribution

When you write on GitHub — issue body, comment, PR body, PR comment — start with an attribution line on its own line: `«Agent <name> writing»` (or `«Agent Persona <name> writing»` when acting under a defined persona).
