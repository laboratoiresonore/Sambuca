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
COMPOSE_FILES := docker-compose.yml:ai.yml:cloud.yml:office.yml:comms.yml:gpu.cpu.yml

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

.PHONY: lint-python
lint-python: ## ruff the flasher
	@cd $(FLASHER_DIR) && ruff check src tests
	@echo "python: clean"

.PHONY: test
test: ## Run the flasher test suite
	@cd $(FLASHER_DIR) && python -m pytest -q

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

.PHONY: pin-images
pin-images: ## Rewrite .env.example with @sha256: digests
	@command -v docker >/dev/null || { echo "pin-images needs docker"; exit 1; }
	@echo "This rewrites compose/.env.example in place. Commit first."
	@read -p "Continue? [y/N] " a; [ "$$a" = y ] || exit 1
	@cp $(COMPOSE_DIR)/.env.example $(COMPOSE_DIR)/.env.example.bak
	@: > $(COMPOSE_DIR)/.env.example.new
	@while IFS= read -r line; do \
		case "$$line" in \
			*_IMAGE=*) \
				key=$${line%%=*}; ref=$${line#*=}; base=$${ref%%@*}; \
				digest=$$(docker buildx imagetools inspect "$$base" 2>/dev/null \
					| awk '/^Digest:/{print $$2; exit}'); \
				if [ -n "$$digest" ]; then \
					echo "$$key=$$base@$$digest" >> $(COMPOSE_DIR)/.env.example.new; \
				else \
					echo "$$line" >> $(COMPOSE_DIR)/.env.example.new; \
				fi;; \
			*) echo "$$line" >> $(COMPOSE_DIR)/.env.example.new;; \
		esac; \
	done < $(COMPOSE_DIR)/.env.example
	@mv $(COMPOSE_DIR)/.env.example.new $(COMPOSE_DIR)/.env.example
	@echo "pinned. Review the diff before committing."

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
