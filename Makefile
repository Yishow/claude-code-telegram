.PHONY: install dev test lint format clean help run run-debug \
        run-remote remote-attach remote-stop \
        daemon-up daemon-install daemon-start daemon-stop daemon-restart daemon-status daemon-logs daemon-down daemon-uninstall daemon-print daemon-linger \
        menu status feature-new sync-main repair-main sync sync-branch sync-abort sync-continue stash-pop \
        maintain

UPSTREAM_REPO := https://github.com/RichardAtCT/claude-code-telegram.git
FORK_WORKFLOW := scripts/fork_workflow.sh
SYSTEMD_SERVICE := scripts/systemd_user_service.sh

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
	@echo "Fork workflow (upstream sync)"
	@echo "  menu           Open lazy menu (AUTO_YES=1 by default; set AUTO_YES=0 for prompts)"
	@echo "  status         Show divergence from upstream"
	@echo "  feature-new    Create feature branch from latest main (NAME=<name>)"
	@echo "  sync-main      Sync local main with upstream/main and push to origin/main"
	@echo "  repair-main    Repair main private commits (reset main + switch to new feature/*)"
	@echo "  sync           Sync main, then rebase current feature branch onto main"
	@echo "  sync-branch    Rebase current feature branch onto local main"
	@echo "  sync-abort     Abort an in-progress rebase"
	@echo "  sync-continue  Continue after resolving rebase conflicts"
	@echo "  stash-pop      Restore latest auto-stash created by workflow"
	@echo ""
	@echo "Maintenance"
	@echo "  maintain       sync + dev + lint + test (fails fast)"
	@echo ""
	@echo "Remote (Mac Mini / SSH)"
	@echo "  run-remote     Start bot in tmux on remote Mac (unlocks keychain)"
	@echo "  remote-attach  Attach to running bot tmux session"
	@echo "  remote-stop    Stop the bot tmux session"
	@echo ""
	@echo "Daemon (Linux systemd --user)"
	@echo "  daemon-up        One-click install + enable + restart + status"
	@echo "  daemon-install   Install/update user service file"
	@echo "  daemon-start     Start service"
	@echo "  daemon-stop      Stop service"
	@echo "  daemon-restart   Restart service"
	@echo "  daemon-status    Show service status"
	@echo "  daemon-logs      Tail logs"
	@echo "  daemon-down      Stop + disable service"
	@echo "  daemon-uninstall Remove user service file"
	@echo "  daemon-print     Show generated service file"
	@echo "  daemon-linger    Enable lingering (keeps running after logout)"

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
install:
	uv sync --no-dev

dev:
	uv sync --extra dev

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
# Daemon (Linux systemd --user)
# ---------------------------------------------------------------------------
daemon-up:
	@bash $(SYSTEMD_SERVICE) up

daemon-install:
	@bash $(SYSTEMD_SERVICE) install

daemon-start:
	@bash $(SYSTEMD_SERVICE) start

daemon-stop:
	@bash $(SYSTEMD_SERVICE) stop

daemon-restart:
	@bash $(SYSTEMD_SERVICE) restart

daemon-status:
	@bash $(SYSTEMD_SERVICE) status

daemon-logs:
	@bash $(SYSTEMD_SERVICE) logs

daemon-down:
	@bash $(SYSTEMD_SERVICE) down

daemon-uninstall:
	@bash $(SYSTEMD_SERVICE) uninstall

daemon-print:
	@bash $(SYSTEMD_SERVICE) print

daemon-linger:
	@bash $(SYSTEMD_SERVICE) linger

# ---------------------------------------------------------------------------
# Fork workflow (safe upstream sync for fork maintainers)
# ---------------------------------------------------------------------------
menu:  ## Open lazy menu for fork workflow
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) menu

status:  ## Show divergence between your branch and upstream
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) status

feature-new:  ## Create feature branch from latest main (NAME=<feature-name> optional)
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) new-feature "$(NAME)"

sync-main:  ## Sync local main from upstream/main and push to origin/main
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) sync-main

repair-main:  ## Repair main by moving private commits to feature/* then reset main to upstream
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) repair-main "$(NAME)"

sync:  ## Sync main and rebase current feature branch onto main
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) sync

sync-branch:  ## Rebase current feature branch onto local main
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) sync-branch

sync-abort:  ## Abort an in-progress rebase
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) sync-abort

sync-continue:  ## Continue after manually resolving rebase conflicts
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) sync-continue

stash-pop:  ## Restore latest workflow auto-stash
	@UPSTREAM_REPO_DEFAULT="$(UPSTREAM_REPO)" bash $(FORK_WORKFLOW) stash-pop

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------
maintain:  ## sync + dev + lint + test in one step
	@echo "=== Maintenance ==="
	$(MAKE) sync
	$(MAKE) dev
	$(MAKE) lint
	$(MAKE) test
	@echo ""
	@echo "=== Maintenance done ==="
