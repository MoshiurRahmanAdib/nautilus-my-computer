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

Translations are managed on [Weblate](https://hosted.weblate.org/projects/nautilus-my-computer/),
not through direct pull requests. Sign up for a free Weblate account, join the project, pick
your language, and translate directly in the web editor. Approved changes are synced back to
this repository automatically as a pull request, you don't need to touch `po/*.po` files
yourself.

If your language isn't listed yet, use Weblate's "start new translation" option on the
project page.

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
   `i18n: ...`, `l10n: ...`), imperative mood, max 72 characters.

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
