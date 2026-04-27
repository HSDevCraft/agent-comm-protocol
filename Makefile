.DEFAULT_GOAL := help
PYTHON        := python
PIP           := pip
SRC           := src
TESTS         := tests

.PHONY: help setup install lint format type-check test test-fast test-cov clean examples

help:
	@echo ""
	@echo "  Agent Communication Protocol — Available Commands"
	@echo "  ─────────────────────────────────────────────────"
	@echo "  setup        Install all dependencies (core + dev)"
	@echo "  install      Install core dependencies only"
	@echo "  lint         Run ruff linter"
	@echo "  format       Run black + isort formatter"
	@echo "  type-check   Run mypy type checker"
	@echo "  test-fast    Run unit tests only (fast)"
	@echo "  test         Run full test suite"
	@echo "  test-cov     Run tests with HTML coverage report"
	@echo "  examples     Run both example scripts"
	@echo "  clean        Remove build artifacts and caches"
	@echo ""

setup:
	$(PIP) install -e ".[dev]"

install:
	$(PIP) install -r requirements.txt

lint:
	ruff check $(SRC) $(TESTS) examples

format:
	black $(SRC) $(TESTS) examples
	isort $(SRC) $(TESTS) examples

type-check:
	mypy $(SRC) --ignore-missing-imports

test-fast:
	pytest $(TESTS)/unit -v --tb=short -q

test:
	pytest $(TESTS) -v --tb=short

test-cov:
	pytest $(TESTS) -v --tb=short --cov=$(SRC) --cov-report=html --cov-report=term-missing
	@echo "Coverage report: htmlcov/index.html"

examples:
	$(PYTHON) examples/basic_delegation.py
	$(PYTHON) examples/swarm_example.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build *.egg-info
