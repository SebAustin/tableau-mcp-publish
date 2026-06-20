.PHONY: help install build lint typecheck test sidecar-install sidecar-lint sidecar-typecheck sidecar-test ci dev clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install the TypeScript MCP server deps
	npm install

build: ## Compile the TypeScript MCP server to dist/
	npm run build

lint: ## Lint the TypeScript sources
	npm run lint

typecheck: ## Type-check without emitting
	npm run typecheck

test: ## Run the TypeScript test suite
	npm test

sidecar-install: ## Sync the Python sidecar environment (incl. dev extras)
	cd sidecar && uv sync --all-extras

sidecar-lint: ## Ruff-check the sidecar
	cd sidecar && uv run ruff check .

sidecar-typecheck: ## mypy --strict the sidecar
	cd sidecar && uv run mypy --strict .

sidecar-test: ## Run the sidecar test suite
	cd sidecar && uv run pytest -q

ci: build lint test sidecar-lint sidecar-typecheck sidecar-test ## Run the full local CI equivalent

dev: ## Start the MCP server (stdio) + spawn the Python sidecar
	npm run dev

clean: ## Remove build output and caches
	rm -rf dist coverage sidecar/.venv .mypy_cache .ruff_cache .pytest_cache
