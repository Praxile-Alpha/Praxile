PYTHON ?= python
PYTEST ?= $(PYTHON) -B -m pytest

.PHONY: test-fast test-fast-repeat test-resource test-integration test-full test-release check-forked

test-fast:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PRAXILE_TEST_TIMEOUT_SECONDS=15 $(PYTEST) -q -m "not slow and not integration and not resource"

test-fast-repeat:
	$(MAKE) test-fast
	$(MAKE) test-fast
	$(MAKE) test-fast

test-resource-repeat: check-forked
	$(MAKE) test-resource
	$(MAKE) test-resource
	$(MAKE) test-resource

test-integration-repeat: check-forked
	$(MAKE) test-integration
	$(MAKE) test-integration
	$(MAKE) test-integration

check-forked:
	$(PYTHON) -c "import pytest_forked" || (echo "pytest-forked is required; run: python -m pip install -e '.[dev]'" && exit 1)

test-resource: check-forked
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PRAXILE_TEST_TIMEOUT_SECONDS=60 $(PYTEST) -p pytest_forked -q -m "resource" --forked

test-integration: check-forked
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PRAXILE_TEST_TIMEOUT_SECONDS=300 $(PYTEST) -p pytest_forked -q -m "(slow or integration) and not resource" --forked

test-full: check-forked
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PRAXILE_TEST_TIMEOUT_SECONDS=300 $(PYTEST) -p pytest_forked -q --forked

test-release:
	$(PYTHON) -m compileall praxile tests scripts
	$(MAKE) test-fast-repeat
	$(MAKE) test-resource
	$(MAKE) test-integration
	$(PYTHON) scripts/clean_release.py
	$(PYTHON) scripts/clean_release.py --check
