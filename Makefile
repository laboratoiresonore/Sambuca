# sambuca :: developer entrypoints
#
# Nothing here touches a real machine. `make iso` produces an artefact;
# installing it is always an explicit, confirmed act via the flasher.

SHELL := /bin/bash
.DEFAULT_GOAL := help

REPO_ROOT := $(shell pwd)
COMPOSE_DIR := $(REPO_ROOT)/compose
ENGINE_DIR := $(REPO_ROOT)/engine
FLASHER_DIR := $(REPO_ROOT)/apps/flasher
BUILD_DIR := $(REPO_ROOT)/build

SHELL_SCRIPTS := $(shell find $(ENGINE_DIR) -name '*.sh' 2>/dev/null)
COMPOSE_FILES := docker-compose.yml:ai.yml:cloud.yml:office.yml:comms.yml:gpu.cpu.ai.yml:gpu.cpu.cloud.yml

.PHONY: help
help: ## Show this help
	@printf '\nsambuca — make targets\n\n'
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@printf '\n'

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

.PHONY: lint
lint: lint-shell lint-compose lint-python ## Run every linter

.PHONY: lint-shell
lint-shell: ## shellcheck every engine script
	@command -v shellcheck >/dev/null || { echo "shellcheck not installed"; exit 1; }
	@shellcheck --severity=warning --external-sources $(SHELL_SCRIPTS)
	@echo "shell: clean"

.PHONY: lint-compose
lint-compose: ## Validate the compose chain renders
	@cd $(COMPOSE_DIR) && \
		COMPOSE_FILE=$(COMPOSE_FILES) COMPOSE_PATH_SEPARATOR=: \
		docker compose --env-file .env.example config --quiet
	@echo "compose: valid"

# ruff is configured ONCE at the repository root, and every path is named here.
# Configured per-package, everything outside apps/flasher/ fell back to whatever
# the installed ruff defaulted to — so the paths and the config have to agree.
.PHONY: lint-python
lint-python: ## ruff the flasher, the tools and the appliance tests
	@ruff check apps/flasher/src apps/flasher/tests tools tests
	@echo "python: clean"

# ---------------------------------------------------------------------------
# `check` IS THE ENTRY POINT, and it delegates rather than repeating itself.
#
# CONTRIBUTING.md told every contributor to run `make check` and there was no
# such target — the first instruction in the file failed with "No rule to make
# target". Adding one that re-listed the checks would have created a second
# list to drift against tools/preflight.sh, which already IS the list, already
# names the three things it cannot run locally, and already exits non-zero when
# a tool is merely missing rather than reporting a partial pass as success.
# ---------------------------------------------------------------------------
.PHONY: check
check: ## Everything CI runs that does not need a Docker runner (start here)
	@bash tools/preflight.sh

.PHONY: test
test: test-flasher test-appliance test-shell ## Run every test suite, both trees

# BOTH TREES, AND THIS IS WHY. `tests/` holds what tests the APPLIANCE;
# apps/flasher/tests holds what tests the flasher. `make test` ran only the
# second, so every appliance test — the beacon's, the recovery-key chain, the
# backup-password chain, the hardening ratchet — passed locally without ever
# being executed here. That is the same shape as the CI workflow that named one
# tree and hid 21 passing tests from itself.
.PHONY: test-flasher
test-flasher: ## Run the flasher test suite
	@cd $(FLASHER_DIR) && python -m pytest -q

.PHONY: test-appliance
test-appliance: ## Run the appliance test suite (tests/)
	@python -m pytest tests -q -m "not slow"

# BY GLOB, NEVER BY NAME. This used to run test-update-guard.sh alone, so a new
# suite was invisible until somebody remembered to add it — and nobody would,
# because nothing failed when they did not.
.PHONY: test-shell
test-shell: ## Run every tests/test-*.sh suite
	@fail=0; for t in tests/test-*.sh; do \
		printf '  %s\n' "$$t"; bash "$$t" >/dev/null || { echo "  FAILED: $$t"; fail=1; }; \
	done; exit $$fail

.PHONY: test-guard
test-guard: ## Feed the update guard poisoned updates and assert it refuses them
	@bash tests/test-update-guard.sh

.PHONY: scan-images
scan-images: ## Scan the running images for fixable HIGH/CRITICAL vulnerabilities
	@bash tools/scan-images.sh --json $(BUILD_DIR)/vuln-report.json

.PHONY: vuln-baseline
vuln-baseline: ## Re-record the accepted vulnerability floor (review the diff!)
	@echo "This LOWERS OR RAISES the floor the CI gate enforces."
	@echo "Raising it hides a regression. Review the diff before committing."
	@python3 tools/vuln-gate.py $(BUILD_DIR)/trivy-reports --update

.PHONY: check
check: lint test ## Everything CI runs

# ---------------------------------------------------------------------------
# Image pinning
#
# `docker compose config` resolves tags but does not pin them. A "zero-config
# installer" that produces different software on two machines flashed a month
# apart is not reproducible, and the GitOps sync has nothing stable to validate
# against. Digests are the release artefact; tags are the dev convenience.
# ---------------------------------------------------------------------------

# No docker dependency: tools/verify-images.py speaks the registry API directly,
# because the machine cutting a release is frequently not a machine with a
# Docker daemon, and a release check that only runs sometimes is not a check.
#
# Exit 0 = all resolved; 1 = a third-party reference is BROKEN; 2 = only
# first-party references are unpublished (a known pre-release state).

.PHONY: verify-images
verify-images: ## Resolve every image reference and report its digest
	@python3 tools/verify-images.py $(COMPOSE_DIR)/.env.example \
		--json $(BUILD_DIR)/image-report.json || { \
		rc=$$?; \
		if [ $$rc -eq 2 ]; then \
			echo; echo "(exit 2: only first-party images unpublished — not a release blocker yet)"; \
		fi; \
		exit $$rc; }

.PHONY: check-upstreams
check-upstreams: ## Probe every external coupling in docs/MAINTENANCE.md for drift
	@python3 tools/check-upstreams.py --json $(BUILD_DIR)/drift-report.json || { \
		rc=$$?; \
		if [ $$rc -eq 2 ]; then \
			echo; echo "(exit 2: known-pending only — not drift)"; \
		fi; \
		exit $$rc; }

.PHONY: pin-images
pin-images: ## Rewrite .env.example with @sha256: digests
	@# NO DOCKER. This used to shell out to `docker buildx imagetools inspect`,
	@# which needs a running daemon - the exact dependency verify-images.py was
	@# written to avoid, and a fair explanation for why nothing was ever pinned:
	@# the release step only worked on a machine that happened to have Docker.
	@# One implementation of resolution, reused, so the pin and the check can
	@# never disagree about what a reference resolves to.
	@echo "This rewrites compose/.env.example in place. Commit first."
	@read -p "Continue? [y/N] " a; [ "$$a" = y ] || exit 1
	@python3 tools/verify-images.py $(COMPOSE_DIR)/.env.example --pin
	@echo "Review the diff before committing."

# ---------------------------------------------------------------------------
# Artefacts
# ---------------------------------------------------------------------------

.PHONY: iso
iso: ## Rebuild a Debian netinst ISO with the sambuca payload embedded
	@$(ENGINE_DIR)/autoinstall/build-iso.sh --output $(BUILD_DIR)

.PHONY: bundle
bundle: ## Produce the git bundle the installer stages onto the appliance
	@mkdir -p $(BUILD_DIR)
	@git bundle create $(BUILD_DIR)/sambuca.bundle main
	@echo "$(BUILD_DIR)/sambuca.bundle"

.PHONY: flasher
flasher: ## Install the flasher in editable mode
	@cd $(FLASHER_DIR) && pip install -e '.[dev]'

# ---------------------------------------------------------------------------
# Local inspection
# ---------------------------------------------------------------------------

.PHONY: profile
profile: ## Run the hardware profiler against THIS machine, writing nothing
	@$(ENGINE_DIR)/hardware-detect.sh --print --dry-run --no-lock

.PHONY: profile-json
profile-json: ## Same, as JSON
	@$(ENGINE_DIR)/hardware-detect.sh --json --dry-run --no-lock --quiet

.PHONY: clean
clean: ## Remove build artefacts
	@rm -rf $(BUILD_DIR) $(FLASHER_DIR)/.pytest_cache $(FLASHER_DIR)/build
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "clean"
