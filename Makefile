# Makefile - Nautilus My Computer Extension
#
# Packaging entry point (AUR, .deb, .rpm, ...). Installs system-wide under
# PREFIX, honouring DESTDIR for staged package builds:
#
#   make build
#   make DESTDIR="$pkgdir" PREFIX=/usr install
#
# For local dev iteration against ~/.local, see CLAUDE.md's "Install and test
# cycle" instead, this Makefile targets system package installs only.

PREFIX  ?= /usr
DESTDIR ?=

EXT_FILE     := nautilus-my-computer.py
PKG_DIR      := nautilus_my_computer
SCHEMA_ID    := io.github.yannmasoch.nautilus-my-computer
SCHEMA_FILE  := $(SCHEMA_ID).gschema.xml
GETTEXT_DOMAIN := nautilus-my-computer

NAUTILUS_EXT_DIR := $(DESTDIR)$(PREFIX)/share/nautilus-python/extensions
SCHEMA_DIR       := $(DESTDIR)$(PREFIX)/share/glib-2.0/schemas
LOCALE_DIR       := $(DESTDIR)$(PREFIX)/share/locale

PY_FILES := $(EXT_FILE) $(wildcard $(PKG_DIR)/*.py)
PO_FILES := $(wildcard po/*.po)
MO_FILES := $(patsubst po/%.po,build/locale/%/LC_MESSAGES/$(GETTEXT_DOMAIN).mo,$(PO_FILES))

.PHONY: all build check install uninstall clean

all: build

build: check $(MO_FILES)

check:
	PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile $(PY_FILES)

build/locale/%/LC_MESSAGES/$(GETTEXT_DOMAIN).mo: po/%.po
	@mkdir -p "$(dir $@)"
	msgfmt "$<" -o "$@"

install: build
	install -Dm644 $(EXT_FILE) "$(NAUTILUS_EXT_DIR)/$(EXT_FILE)"
	@for f in $(PKG_DIR)/*.py; do \
		install -Dm644 "$$f" "$(NAUTILUS_EXT_DIR)/$$f"; \
	done
	install -Dm644 $(SCHEMA_FILE) "$(SCHEMA_DIR)/$(SCHEMA_FILE)"
	@for mo in $(MO_FILES); do \
		lang=$$(echo "$$mo" | sed -n 's|build/locale/\(.*\)/LC_MESSAGES/.*|\1|p'); \
		install -Dm644 "$$mo" "$(LOCALE_DIR)/$$lang/LC_MESSAGES/$(GETTEXT_DOMAIN).mo"; \
	done
	@if [ -z "$(DESTDIR)" ]; then \
		glib-compile-schemas "$(SCHEMA_DIR)"; \
	fi

uninstall:
	rm -f "$(NAUTILUS_EXT_DIR)/$(EXT_FILE)"
	rm -rf "$(NAUTILUS_EXT_DIR)/$(PKG_DIR)"
	rm -f "$(SCHEMA_DIR)/$(SCHEMA_FILE)"
	@for po in $(PO_FILES); do \
		lang=$$(basename "$$po" .po); \
		rm -f "$(LOCALE_DIR)/$$lang/LC_MESSAGES/$(GETTEXT_DOMAIN).mo"; \
	done
	@if [ -z "$(DESTDIR)" ]; then \
		glib-compile-schemas "$(SCHEMA_DIR)"; \
	fi

clean:
	rm -rf build
	find . -name '__pycache__' -type d -exec rm -rf {} +
