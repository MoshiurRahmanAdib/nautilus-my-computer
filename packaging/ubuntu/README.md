# Ubuntu PPA packaging

Source packaging for `ppa:yannmasoch/nautilus-my-computer`, targeting noble (24.04 LTS),
oracular (24.10), and plucky (25.04).

## One-time setup (maintainer machine)

```shell
sudo apt install devscripts dpkg-dev debhelper gnupg
```

A GPG key must be configured and registered with your Launchpad account (`debuild` signs the
source package with it; unsigned uploads are rejected by Launchpad).

## Build and upload

```shell
packaging/ubuntu/build-and-upload.sh 0.11.0            # build only, output in ./ppa-build
packaging/ubuntu/build-and-upload.sh 0.11.0 --upload    # build and dput to the PPA
```

This builds one signed source package per target series (version suffixed `~<series>1`,
e.g. `0.11.0-1~noble1`) from a tarball of the current checked-out commit, since the PPA build
tarball must match what actually gets built. Tag and push the release commit first.

## Layout

- `debian/control` - package metadata and dependencies (`python3-nautilus`, `gir1.2-adw-1`, ...)
- `debian/rules` - delegates to the same `make build` / `make install` used by the AUR, Fedora,
  and openSUSE packages
- `debian/changelog` - base entry; `build-and-upload.sh` stamps the real version/distribution
  per series via `dch` at build time
- `debian/copyright` - DEP-5 format, MIT license
- `debian/watch` - `uscan` rule tracking GitHub release tags

## Local build sanity check (no Launchpad needed)

```shell
cp -r packaging/ubuntu/debian ./debian
dpkg-buildpackage -us -uc -b
rm -rf ./debian ../nautilus-my-computer_*.deb ../nautilus-my-computer_*.buildinfo ../nautilus-my-computer_*.changes
```

Installs identically to the other distros (`nautilus-my-computer.py` and `nautilus_my_computer/`
under `/usr/share/nautilus-python/extensions/`, schema under `/usr/share/glib-2.0/schemas/`).
