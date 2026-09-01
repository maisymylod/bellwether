# Everything here runs offline. No API key is needed for any target.

ENGINE := engine
WEB    := web
PY     := $(ENGINE)/.venv/bin/python
PIP    := $(ENGINE)/.venv/bin/pip

.PHONY: help setup check test lint typecheck eval gate sweep api web dev demo clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install both halves
	@if command -v uv >/dev/null 2>&1; then \
		uv venv --python 3.13 $(ENGINE)/.venv && \
		uv pip install --python $(PY) -e "$(ENGINE)[dev]"; \
	else \
		python3 -m venv $(ENGINE)/.venv && \
		$(PIP) install --upgrade pip && $(PIP) install -e "$(ENGINE)[dev]"; \
	fi
	cd $(WEB) && npm install

check: lint typecheck test gate ## Everything CI runs

lint: ## ruff and eslint
	cd $(ENGINE) && .venv/bin/ruff check .
	cd $(ENGINE) && .venv/bin/ruff format --check .
	cd $(WEB) && npm run lint

typecheck: ## mypy --strict and tsc
	cd $(ENGINE) && .venv/bin/mypy
	cd $(WEB) && npm run typecheck

test: ## pytest and vitest
	cd $(ENGINE) && .venv/bin/pytest -q
	cd $(WEB) && npm test

eval: ## Score the golden set and print the metrics
	cd $(ENGINE) && .venv/bin/python -m bellwether.cli eval

gate: ## Score the golden set and fail if a threshold is breached
	cd $(ENGINE) && .venv/bin/python -m bellwether.cli gate

sweep: ## Measure what self-consistency sampling buys
	cd $(ENGINE) && .venv/bin/python -m bellwether.cli sweep

api: ## Start the engine on 8788
	cd $(ENGINE) && .venv/bin/python -m bellwether.cli serve --reload

web: ## Start the console on 5273
	cd $(WEB) && npm run dev

demo: ## One run, printed to the terminal
	cd $(ENGINE) && .venv/bin/python -m bellwether.cli ask \
		"How did Novaline Systems perform in 2025Q3 and what is the main risk?"

clean:
	rm -rf $(ENGINE)/.venv $(ENGINE)/.pytest_cache $(ENGINE)/.mypy_cache \
	       $(ENGINE)/.ruff_cache $(WEB)/node_modules $(WEB)/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
