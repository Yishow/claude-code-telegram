---
name: fork-feature-delivery
description: Execute end-to-end feature delivery in this fork from requirement to commit. Use when user asks for a new feature or bug fix and expects direct implementation with fork stack workflow, verification, and a final git commit.
---

# Fork Feature Delivery

## Objective

Ship a requested change in this fork without asking the user to repeat git workflow details.

## Execution Contract

- Do not develop on `main`.
- Always create a new feature branch for each task.
- Always use cumulative fork stack workflow.
- Implement directly unless user explicitly asks for planning only.
- Verify changes before commit.
- Commit with a clear, scoped, detailed Traditional Chinese message.

## Standard Flow

1. Clarify only blocking requirements.
Ask only when ambiguity changes architecture, UX, or safety behavior.

2. Create/sync feature branch with fork workflow.
Create a new branch name based on the task scope for this turn.
Examples: `feature/daemon-restart-stability`, `feature/copilot-timeout-hardening`.

Preferred:
```bash
make stack-feature BASE=feature/fork-workflow-menu NEW=feature/<feature-name>
```
If branch already exists:
```bash
make stack-sync BASE=feature/fork-workflow-menu
git checkout feature/<feature-name>
```

3. Implement minimal, coherent code changes.
- Reuse existing patterns in this repository.
- Keep security and boundary checks intact.
- Avoid unrelated refactors.

4. Update tests and docs required by the change.
- Add or adjust unit/integration tests for behavior changes.
- Update README/docs when command behavior or workflow semantics change.

5. Validate.
Run relevant checks in this order:
```bash
uv run --extra dev pytest <targeted-tests>
uv run --extra dev flake8 <touched-files>
```
If environment issues block full checks, run fallback syntax validation and report the blocker:
```bash
python3 -m compileall <touched-python-files>
```

6. Review diff quality.
- Confirm only intended files changed.
- Remove temporary/debug edits.
- Ensure messages and command help text match final behavior.

7. Commit.
```bash
git add <intended-files>
git commit -m "<type>: <繁中精簡主旨>" -m "<繁中詳細說明：變更內容、原因、影響與驗證>"
```
Commit message style:
- `feat:` new behavior
- `fix:` bug fix
- `refactor:` internal-only behavior-preserving changes
- `docs:` documentation-only
- `test:` tests-only

Commit language and detail requirements:
- Use Traditional Chinese (繁體中文).
- Subject should be concise and scoped.
- Body should explain at least:
  - what changed
  - why it changed
  - impact/risk
  - verification performed

8. Report completion.
Provide:
- what changed
- verification results
- exact commit hash
- any known follow-up risks

## Safety Rules

- Never use destructive git resets unless user explicitly asks.
- Never push with plain `--force`; use `--force-with-lease` after rebase.
- Never bypass approved directory boundaries or security guardrails.
