# Contributing

Thanks for considering a contribution to **My Computer for Nautilus**.

## Design philosophy

We follow the [GNOME Human Interface Guidelines](https://developer.gnome.org/hig/) and
Adwaita design and interaction patterns as closely as possible, the goal is for **My
Computer** to feel like a native part of Nautilus, not a bolted-on plugin. Prefer existing
libadwaita/GTK widgets and style classes over custom CSS or bespoke widgets, and check
that any new interaction matches how Nautilus itself already behaves.

## Before you open a pull request

**Base your pull request on the `dev` branch, not `main`.** `main` only receives tested,
released changes; `dev` is where ongoing work and contributions land before a release.
If your PR is opened against `main` by mistake, you can retarget it to `dev` from the
pull request's "Edit" button on GitHub.

## Translations

Adding or improving a translation is the easiest way to contribute.

The canonical list of translatable strings lives in `po/nautilus-my-computer.pot`,
generated from the source with `make pot`. Don't copy an existing `.po` file as a
starting point, `.po` files can lag behind the source strings between releases, and a
copy just carries that gap forward.

You'll need `gettext` installed to run the `make` targets below:
```bash
# Arch:           sudo pacman -S gettext
# Fedora:         sudo dnf install gettext
# Debian/Ubuntu:  sudo apt install gettext
```

**New language:**
1. `make pot` to (re)generate `po/nautilus-my-computer.pot`.
2. Copy it to `po/<lang>.po` (e.g. `po/hu.po` for Hungarian), and fill in the header
   fields (`Language`, `Language-Team`, `Last-Translator`, `PO-Revision-Date`).
3. Translate each `msgstr` below its matching `msgid`. Leave `msgid` lines untouched.
4. Open a pull request with just the `.po` file change.

**Updating an existing language after source strings changed:**
1. `make po-update` regenerates the `.pot` and merges it into every `po/*.po` via
   `msgmerge`, adding new strings, marking removed ones as obsolete (`#~`), and
   flagging uncertain matches as `#, fuzzy` for review.
2. Fix or clear any `#, fuzzy` entries for your language, translate new empty `msgstr`s,
   and remove any `#~` (obsolete) entries for your language.
3. Open a pull request with just the `.po` file change.

You don't need to compile `.mo` files or update the version, that's handled at release
time. CI checks that each `.po` file's strings match the current `.pot`, so run
`make po-update` before opening a PR if source strings have changed since your last sync.

## Code contributions

1. Open an issue first for anything beyond a small fix, so we can agree on the approach.
2. Keep the single-file structure (`nautilus-my-computer.py`), don't split it into modules.
3. Run before committing:
   ```bash
   ruff format
   ruff check
   ```
4. Test your change against a real Nautilus session:
   ```bash
   cp nautilus-my-computer.py ~/.local/share/nautilus-python/extensions/
   rm -rf ~/.local/share/nautilus-python/extensions/__pycache__
   nautilus -q; sleep 1 && nautilus
   ```
5. Commit messages follow `<type>: <short description>` (e.g. `fix: ...`, `feat: ...`,
   `i18n: ...`), imperative mood, max 72 characters.

## Reporting bugs

Open an issue with:
- Nautilus version and GNOME version
- Steps to reproduce, if you can
- Relevant log lines, with debug logging enabled:
  ```bash
  DEBUG=1 DISPLAY=:0 nohup nautilus --no-desktop > /tmp/nautilus.log 2>&1 &
  ```
  then grep for `MyComputer:` in `/tmp/nautilus.log`

## License

By contributing, you agree your contribution is licensed under this project's MIT license.

---

**My Computer** started as a simple solo project and quickly grew into something
community-driven. Every translation, bug report, and pull request makes it better.
Thanks for being part of that, we're glad you're here!
