Name:           nautilus-my-computer
Version:        0.12.1
Release:        0
Summary:        My Computer for Nautilus, what GNOME Files should have always been

License:        MIT
URL:            https://github.com/yannmasoch/nautilus-my-computer
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  gettext-tools
BuildRequires:  make
BuildRequires:  python3

Requires:       python3-nautilus
Requires:       libadwaita

%description
My Computer is a custom view for GNOME Files (Nautilus), showing all your
drives, volumes, and network mounts with usage levels in one clean panel:
System, Devices and Drives, Removable Devices, and Network Volumes, with
usage bars, context menus, live refresh, and mount/unmount/eject actions.

%prep
%autosetup

%build
make build

%install
%make_install
%find_lang %{name}

%files -f %{name}.lang
%license LICENSE
%dir %{_datadir}/nautilus-python
%dir %{_datadir}/nautilus-python/extensions
%{_datadir}/nautilus-python/extensions/nautilus-my-computer.py
%{_datadir}/nautilus-python/extensions/nautilus_my_computer/
%dir %{_datadir}/glib-2.0
%dir %{_datadir}/glib-2.0/schemas
%{_datadir}/glib-2.0/schemas/io.github.yannmasoch.nautilus-my-computer.gschema.xml

%changelog
* Mon Jul 13 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.12.1-0
- Replace the two-state view-mode button with a segmented Grid/List/Column
  switcher, so all three views are one click away

* Mon Jul 13 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.12.0-0
- Add an early, opt-in Column View (beta): browse folders as Miller-style
  columns alongside the normal file view and the Computer panel

* Wed Jul 08 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.9-0
- Fix the extension failing to load entirely on GNOME Files 47 and 48,
  where the Computer view never appeared and no error was visible outside
  a terminal (issue #61)
- Fix the Computer icon getting permanently stuck next to a tab's label
  after navigating that tab away from the Computer view (issue #29)
- Fix the address bar showing no icon for Computer (with a stray "/" in
  front of the name instead) on some systems, confirmed on Fedora 41

* Tue Jul 07 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.8-0
- Fix the install script failing with a GitHub rate-limit error (429) for
  users on shared or VPN networks, by downloading the extension as a single
  archive instead of many separate files. Also fix `bash install.sh` run
  from a manually cloned copy being incorrectly treated as an online
  install instead of installing from the local files.

* Tue Jul 07 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.7-0
- Fix a drive being listed twice in the Computer view when it is mounted
  through autofs (e.g. some fstab/NAS network share setups): the autofs
  trigger mount and the real filesystem mounted on top of it were both
  shown as separate cards. The scanner now only accepts known real-storage,
  network, or optical filesystem types, imports GIO's shadowed-mount
  signal, and collapses any leftover same-mountpoint duplicates to the
  visible mount (issue #57).

* Mon Jul 06 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.6-0
- Fix a permanently blank Computer view inside file-picker dialogs
  (NautilusFileChooser), and a multi-tab regression found while fixing it.
  Navigation detection now watches window/slot location instead of window
  title (issue #55).

* Mon Jul 06 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.5-0
- Add GNOME/Nautilus version-detection helpers and fix two GNOME 47
  compatibility issues: sidebar row resolves NautilusGtkSidebarRow (47)
  or NautilusSidebarRow (48+), and the Preferred Folders/disk-grid
  FlowBox now uses halign=FILL unconditionally.

* Mon Jul 06 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.4-0
- Fix OBS auto-rebuild: the workflow's rebuild_package step only
  re-triggers a build of already-committed sources, it never re-runs
  the _service, so every tag push kept rebuilding the same stale
  content. Switched to trigger_services, which re-runs the _service
  (re-fetching the spec and tarball from GitHub) and auto-triggers a
  rebuild once the content changes.

* Mon Jul 06 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.3-0
- Fix broken CI: pin AUR deploy action to v4.1.3 (no floating v4 tag
  exists), add build-essential to the PPA workflow's installed
  packages, fix a curl flag conflict in the COPR trigger workflow

* Mon Jul 06 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.2-0
- Trigger COPR/AUR/Ubuntu PPA/openSUSE OBS builds directly on version tag
  push instead of a manually-published GitHub release
- Auto-create a GitHub Release with changelog notes on tag push

* Sun Jul 06 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.1-0
- Add Ubuntu PPA packaging, sync EXT_VERSION with pyproject.toml

* Sun Jul 05 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.11.0-0
- Add Makefile and AUR/Fedora/openSUSE packaging

* Sun Jul 05 2026 Yann Masoch <231734284+yannmasoch@users.noreply.github.com> - 0.10.1-0
- Initial package
