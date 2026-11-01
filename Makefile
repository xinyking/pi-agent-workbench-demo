PYTHON ?= python3
NODE ?= node

.PHONY: run test test-python test-js

run:
	$(PYTHON) -m workbench --port 4310 $(ARGS)

test: test-python test-js

test-python:
	$(PYTHON) -m unittest discover -s tests -q

test-js:
	$(NODE) --test tests/test_*.mjs
