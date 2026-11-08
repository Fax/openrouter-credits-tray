# openrouter-credits-tray: install, test and packaging targets.
#
#   make test        run the unit tests
#   make install     install for the current user (~/.local/bin + autostart)
#   make uninstall   remove the user-local install (keeps your keyring entry)
#   make dist        source tarball in dist/
#   make deb         Debian package in dist/
#   make release V=patch   bump the version, tag and push (patch|minor|major|X.Y.Z)
#   make clean       drop build artefacts

SHELL := /bin/bash
PY := /usr/bin/python3
NAME := openrouter-credits-tray
APP := $(NAME).py
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' $(APP) | head -1)
DIST := dist
TARBALL := $(DIST)/$(NAME)-$(VERSION).tar.gz

.PHONY: all test install uninstall dist deb icons release clean help

all: test

help:
	@sed -n '2,10p' $(MAKEFILE_LIST)

test:
	$(PY) -m unittest discover -s tests -v

install:
	./install.sh --autostart

uninstall:
	-@systemctl --user stop $(NAME) 2>/dev/null || true
	-@pkill -f "$(HOME)/.local/bin/$(APP)" 2>/dev/null || true
	rm -f $(HOME)/.local/bin/$(APP)
	rm -f $(HOME)/.config/autostart/$(NAME).desktop $(HOME)/.config/autostart/openrouter-credits.desktop
	@echo "Removed. The stored key stays in your keyring;"
	@echo "drop it with: $(APP) --clear-key"

icons:
	$(PY) packaging/make-icons.py

dist: $(TARBALL)

$(TARBALL): $(APP) README.md LICENSE install.sh Makefile packaging tests docs
	@mkdir -p $(DIST)
	@rm -rf $(DIST)/$(NAME)-$(VERSION)
	@mkdir -p $(DIST)/$(NAME)-$(VERSION)
	@tar -cf - --exclude='__pycache__' --exclude='.git' \
		$(APP) README.md LICENSE install.sh Makefile packaging tests docs \
		| tar -xf - -C $(DIST)/$(NAME)-$(VERSION)
	@tar -czf $(TARBALL) -C $(DIST) $(NAME)-$(VERSION)
	@rm -rf $(DIST)/$(NAME)-$(VERSION)
	@echo "==> $(TARBALL)"

deb: $(APP) README.md LICENSE
	./packaging/build-deb.sh

release:
	@test -n "$(V)" || { echo 'usage: make release V=patch|minor|major|X.Y.Z'; exit 2; }
	./packaging/release.sh $(V)

clean:
	rm -rf $(DIST) packaging/__pycache__ tests/__pycache__ __pycache__
