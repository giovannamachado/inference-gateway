PY ?= python
.PHONY: install test bench lint up
install: ; $(PY) -m pip install -e ".[dev]"
test: ; $(PY) -m pytest -q
lint: ; ruff check src tests bench
bench: ; $(PY) bench/threshold_curve.py && $(PY) bench/load_test.py
up: ; docker compose up --build
