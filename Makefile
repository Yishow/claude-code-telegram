.PHONY: install dev test lint format clean help run run-debug \
        run-remote remote-attach remote-stop \
        sync sync-abort sync-continue \
        status maintain

UPSTREAM_REPO := https://github.com/RichardAtCT/claude-code-telegram.git
UPSTREAM_BRANCH := upstream/main

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Development"
	@echo "  install        Install production dependencies"
	@echo "  dev            Install development dependencies"
	@echo "  run            Run the bot"
	@echo "  run-debug      Run with debug logging"
	@echo "  test           Run tests"
	@echo "  lint           Run linting checks (black / isort / flake8)"
	@echo "  format         Auto-format code (black + isort)"
	@echo "  clean          Remove build artefacts and caches"
	@echo ""
	@echo "Upstream sync  (rebase your commits on top of upstream)"
	@echo "  status         Show divergence from upstream"
	@echo "  sync           Fetch upstream and rebase your commits onto it"
	@echo "  sync-abort     Abort an in-progress rebase"
	@echo "  sync-continue  Continue after resolving rebase conflicts"
	@echo ""
	@echo "Maintenance"
	@echo "  maintain       sync + dev + lint + test in one step"
	@echo ""
	@echo "Remote (Mac Mini / SSH)"
	@echo "  run-remote     Start bot in tmux on remote Mac (unlocks keychain)"
	@echo "  remote-attach  Attach to running bot tmux session"
	@echo "  remote-stop    Stop the bot tmux session"

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
install:
	uv sync --no-dev

dev:
	uv sync

# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------
test:
	uv run pytest --no-cov

lint:
	uv run black --check src tests
	uv run isort --check-only src tests
	uv run flake8 src tests

format:
	uv run black src tests
	uv run isort src tests

# ---------------------------------------------------------------------------
# Artefacts
# ---------------------------------------------------------------------------
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov/ .pytest_cache/ dist/ build/

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
run:
	uv run claude-telegram-bot

run-debug:
	uv run claude-telegram-bot --debug

# ---------------------------------------------------------------------------
# Remote (Mac Mini)
# ---------------------------------------------------------------------------
run-remote:
	security unlock-keychain ~/Library/Keychains/login.keychain-db
	tmux new-session -d -s claude-bot 'uv run claude-telegram-bot'
	@echo "Bot started in tmux session 'claude-bot'"
	@echo "  Attach: make remote-attach"
	@echo "  Stop:   make remote-stop"

remote-attach:
	tmux attach -t claude-bot

remote-stop:
	tmux kill-session -t claude-bot

# ---------------------------------------------------------------------------
# Upstream sync  (rebase strategy — keeps your commits on top cleanly)
# ---------------------------------------------------------------------------

# Guard: fail early if a rebase / merge is already in progress
_guard-no-rebase:
	@if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then \
		echo "[ERROR] A rebase is already in progress."; \
		echo "        Resolve conflicts, then run: make sync-continue"; \
		echo "        Or abandon it with:          make sync-abort"; \
		exit 1; \
	fi
	@if [ -f .git/MERGE_HEAD ]; then \
		echo "[ERROR] A merge is in progress (possibly from a previous 'make sync')."; \
		echo "        Resolve conflicts and commit, or run: git merge --abort"; \
		exit 1; \
	fi

status:  ## Show divergence between your branch and upstream
	@git remote get-url upstream 2>/dev/null || git remote add upstream $(UPSTREAM_REPO)
	@git fetch upstream -q
	@echo "=== Upstream vs your branch ==="
	@git log --oneline $(UPSTREAM_BRANCH)..HEAD | head -20 | \
		awk 'BEGIN{print "Your commits not in upstream:"} {print "  " $$0} END{if(NR==0)print "  (none — you are up to date)"}'
	@git log --oneline HEAD..$(UPSTREAM_BRANCH) | head -20 | \
		awk 'BEGIN{print "Upstream commits not in your branch:"} {print "  " $$0} END{if(NR==0)print "  (none)"}'

sync: _guard-no-rebase  ## Rebase your commits onto upstream/main (auto-resolves upstream-only files)
	@echo "=== Syncing with upstream (rebase) ==="
	@git remote get-url upstream 2>/dev/null || git remote add upstream $(UPSTREAM_REPO)
	git fetch upstream
	@echo "[INFO] Rebasing onto $(UPSTREAM_BRANCH)..."
	@if git rebase $(UPSTREAM_BRANCH) \
	        -X theirs \
	        --rebase-merges; then \
		echo ""; \
		echo "=== Sync done — your commits are now on top of upstream ==="; \
		echo "Push with: git push --force-with-lease"; \
	else \
		echo ""; \
		echo "[WARN] Rebase conflict in files that need manual review:"; \
		git diff --name-only --diff-filter=U 2>/dev/null | sed 's/^/  /'; \
		echo ""; \
		echo "Steps to resolve:"; \
		echo "  1. Edit the conflicted files above"; \
		echo "  2. git add <resolved-files>"; \
		echo "  3. make sync-continue"; \
		echo "  Or give up: make sync-abort"; \
		exit 1; \
	fi

sync-abort:  ## Abort an in-progress rebase
	git rebase --abort
	@echo "Rebase aborted. Branch is back to its previous state."

sync-continue:  ## Continue after manually resolving rebase conflicts
	@if git diff --name-only --diff-filter=U 2>/dev/null | grep -q .; then \
		echo "[ERROR] Unresolved conflicts remain in:"; \
		git diff --name-only --diff-filter=U | sed 's/^/  /'; \
		exit 1; \
	fi
	GIT_EDITOR=true git rebase --continue
	@echo "=== Rebase step done ==="
	@if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then \
		echo "Rebase still in progress — run 'make sync-continue' again if needed."; \
	else \
		echo "Rebase complete. Push with: git push --force-with-lease"; \
	fi

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------
maintain:  ## sync + dev + lint + test in one step
	@echo "=== 一鍵維護 ==="
	$(MAKE) sync
	$(MAKE) dev
	$(MAKE) lint   || echo "[WARN] Lint issues found — run 'make format' to auto-fix"
	$(MAKE) test   || echo "[WARN] Tests failed"
	@echo ""
	@echo "=== 維護完成 ==="
	@echo "Push with: git push --force-with-lease"
