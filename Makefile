#!/usr/bin/make
PYTHON := /usr/bin/env python

lint:
	@flake8 --exclude hooks/charmhelpers hooks
	@flake8 --exclude hooks/charmhelpers unit_tests
	@charm proof

test:
	@echo Starting tests...
	@$(PYTHON) /usr/bin/nosetests -v --nologcapture --with-coverage unit_tests

sync:
	@charm-helper-sync -c charm-helpers-hooks.yaml
	@charm-helper-sync -c charm-helpers-tests.yaml

publish: lint test
	bzr push lp:charms/swift-storage
	bzr push lp:charms/trusty/swift-storage
