PY      ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin

.PHONY: all setup test demo-p1 demo-p2 eval-p1 eval-p2 lint clean

# One command: set up, test, run both demos and both evaluation gates.
all: setup test eval-p1 demo-p1 demo-p2 eval-p2

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -e ".[dev,mcp-compat]"

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check .

# Part 1: release gate (golden set + thresholds from config/release_gates.yaml)
eval-p1:
	SWDA_AUDIT_HMAC_KEY=dev-only-demo-key $(BIN)/swda-gateway gate

# Part 1: drive the MCP server over stdio with the bundled client
demo-p1:
	SWDA_AUDIT_HMAC_KEY=dev-only-demo-key $(BIN)/swda-gateway demo

# Part 2: run the simulation with the scripted mock LLM and a scripted *human* decision file
demo-p2:
	$(BIN)/collusion-sim run --scenario baseline --decisions scenarios/human_decisions_demo.yaml --out outputs/sample

# Part 2: end-to-end evals + regression check against committed baseline
eval-p2:
	$(BIN)/collusion-sim eval

clean:
	rm -rf $(VENV) .pytest_cache part1_system_design/var/audit part2_case_study/outputs/_tmp
