SHELL := /bin/sh
PYTHON_TEST_IMAGE := openmcp-connectors-python-tests:local
CONNECTORS := $(shell cat connectors.list)

.PHONY: test test-image test-python test-connector build build-python
test: test-python

test-image:
	docker build -f scripts/Dockerfile.test -t $(PYTHON_TEST_IMAGE) .

test-python: test-image
	docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory 2g --pids-limit 256 --tmpfs /tmp:rw,exec,mode=1777 -e RUFF_CACHE_DIR=/tmp/ruff -e MYPY_CACHE_DIR=/tmp/mypy -v "$(CURDIR):/workspace:ro" -w /workspace $(PYTHON_TEST_IMAGE) \
		python scripts/test_connectors.py

test-connector: test-image
	docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory 2g --pids-limit 256 --tmpfs /tmp:rw,exec,mode=1777 -e OPENMCP_TEST_CONNECTOR="$(CONNECTOR)" -v "$(CURDIR):/workspace:ro" -w /workspace $(PYTHON_TEST_IMAGE) \
		sh -ec 'python scripts/test_connectors.py --connector "$$OPENMCP_TEST_CONNECTOR"'

build: build-python

build-python: test-image
	docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges -v "$(CURDIR):/workspace:ro" -w /workspace $(PYTHON_TEST_IMAGE) python scripts/connector_inventory.py check
	@set -e; for connector in $(CONNECTORS); do docker build -f "$$connector/Dockerfile" -t "openmcp-connector-$$connector:test" .; docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory 256m --pids-limit 64 --tmpfs /tmp:rw,noexec,mode=1777 -v "$(CURDIR)/scripts/smoke-image.py:/checks/smoke-image.py:ro" --entrypoint python "openmcp-connector-$$connector:test" /checks/smoke-image.py "$$connector"; done
