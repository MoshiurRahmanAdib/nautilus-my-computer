# Changelog

All notable changes to this project are documented here.

---

## v0.11.1
Ubuntu PPA packaging.

### Added
- Ubuntu `debian/` packaging (`packaging/ubuntu/`), targeting noble, oracular, and plucky,
  built and installed end-to-end on a real Ubuntu 26.04 VM
- `packaging/ubuntu/build-and-upload.sh` to build and `dput` signed source packages
  per series to `ppa:yannmasoch/nautilus-my-computer`
- `ubuntu-validate` CI job building a real `.deb` via `dpkg-buildpackage` and checking
  installed paths with `lintian`

### Fixed
- `EXT_VERSION` in `nautilus_my_computer/main.py` was still `0.10.1`, out of sync with
  `pyproject.toml`; the About page now reports the correct version

---

## v0.11.0
Package-manager distribution: Makefile-driven packaging for AUR, Fedora, and openSUSE.

### Added
- `Makefile` with standard `DESTDIR`/`PREFIX` install/uninstall targets, for use by
  distro packaging instead of the manual `~/.local` dev-loop steps
- AUR `PKGBUILD` (`packaging/aur/`), plus a GitHub Action that publishes it to the
  AUR git repo automatically on each GitHub release
- Fedora RPM `.spec` (`packaging/fedora/`), built, linted, and installed end-to-end
  on a real Fedora VM
- openSUSE RPM `.spec` (`packaging/opensuse/`), built, linted, and installed
  end-to-end on a real openSUSE Tumbleweed VM

### Changed
- Project description updated to "My Computer for Nautilus, what GNOME Files
  should have always been" across README, `pyproject.toml`, and the AUR package

---

## v0.10.1
Sidebar and Preferred Folders polish.

### Added
- Recent and Starred now appear by default in Preferred Folders, right
  after Home, with Network added at the end, each with a proper icon
- Switching your icon theme now updates disk and folder card icons
  immediately, no more restarting Nautilus to see the change

### Fixed
- The sidebar separator line between the Computer row and your other
  places no longer disappears when every native place is hidden
- The drag gutter on folder cards now matches Nautilus's native hover
  highlight instead of looking out of place while dragging

---

## v0.10.0
Preferred Folders can now be reordered by drag-and-drop, plus a big internal refactor.

### Added
- Drag-and-drop reordering for Preferred Folders cards, with a live preview
  of the landing slot as you drag; the new order is persisted to GSettings
  on drop
- Hidden-file detection for disk and folder cards (`.hidden-file` CSS class),
  matching native Nautilus behavior
- Live rename tracking for Preferred Folders via `Gio.FileMonitor` on parent
  directories, auto-correcting stale GSettings URIs when a folder is renamed
  on disk

### Changed
- Folder card grid layout is now compact (single-line label, 42px icon) and
  always shown as a grid, closer to native Nautilus folder cells
- Preferred Folders menu items renamed from "Add/Remove from Preferred" to
  "Pin to My Computer" / "Unpin from My Computer" across all surfaces (card,
  file view, pathbar menu)
- Removed the duplicate "Add/Remove from Bookmarks" item from the folder
  card right-click menu (kept only in the native file-view menu)

### Internal
- Split the monolithic entry file into `nautilus_my_computer/main.py` (app
  state and Nautilus integration), `common.py` (stateless helpers),
  `widgets.py` (`MyComputerDiskCard`/`MyComputerFolderCard`/
  `MyComputerCardSection`), and per-surface target modules (`bookmarks.py`,
  `preferred_folders.py`, `file_view_menu.py`); no behavior change
- Installer now handles the `nautilus_my_computer/` package directory as a
  distinct unit from the entry shim, and reads the local version from
  `nautilus_my_computer/__init__.py`

---

## v0.9.1
The "Open With…" row now does something.

### Added
- "Open With…" on local folder and disk cards now opens an app chooser
  matching native Nautilus's own picker (search, Recommended/Other Apps),
  attached to the Nautilus window, instead of always being greyed out

### Changed
- Preferred Folders card spacing increased from 16px to 24px between columns

### Fixed
- Turkish translation for "Open With…" was missing; added

### Docs
- README now documents the Preferred Folders group (added in v0.9.0), with a
  new screenshot and settings/feature list entries
  
---

## v0.9.0
One-click access to your everyday folders, right from the Computer view.

### Added
- A new "Preferred Folders" group at the top of the Computer panel, with
  cards for Home, Recent, Starred, Network, Documents, Downloads, Music,
  Videos, and Pictures, so you can jump straight to them without digging
  through the sidebar (issue #30)
- This group can be hidden if you'd rather not see it, and power users
  (or distributions shipping their own defaults) can customize which
  folders appear and in what order

---

## v0.8.4
Correct root disk detection on OSTree/bootc systems.

### Fixed
- On OSTree/bootc systems (e.g. stillOS), the disk view could show `/etc` or
  `/var` instead of a proper root disk, since `/proc/mounts` exposes those as
  separate implementation mounts. The root card now reports the real writable
  backing filesystem, and `/etc`, `/var`, and `/sysroot` are hidden from the
  disk list (credit @PizzaLovingNerd, stillOS, PR #44)

---

## v0.8.3
Raises the Python floor to match what the extension actually requires, and refreshes a doc screenshot.

### Changed
- Minimum Python bumped from 3.9 to 3.12. The extension already required
  libadwaita 1.5 (`Adw.PreferencesDialog`) and GNOME 46, so no distro that
  can run it ships Python older than 3.11; the 3.9 floor was no longer
  accurate
- Updated the custom bookmark icons screenshot in `assets/images/`

---

## v0.8.2
Translation prep for the bookmark icon picker.

### Internationalization
- Added translatable strings for the bookmark "Change Icon" menu item and
  picker dialog (Icon, Search icons…, Reset); French translated, other
  languages left for contributors to fill in

---

## v0.8.1
Lets distros and power users set a different default icon for the Computer entry.

### Added
- The Computer icon (in the sidebar and the address bar) can now be changed
  via GSettings, instead of always being the default `computer-symbolic`
  icon. Changes apply instantly, no restart needed. This is mainly useful
  for Linux distributions that want to ship their own icon by default.

---

## v0.8.0
Custom icons for bookmarks, with native right-click integration.

### Added
- Right-click "Change icon" on any sidebar bookmark, with a symbolic icon
  picker dialog; the chosen icon persists across Nautilus restarts and is
  pinned against Nautilus's async icon overwrites (issue #23)

---

## v0.7.13

### Fixed
- Opening a folder whose name contains "Computer" (e.g. an album folder named
  "OK Computer") no longer shows the My Computer view instead of the folder's
  contents; detection now checks the actual `computer:///` location instead of
  matching the window title text (credit @funinkina, issue #38)

---

## v0.7.12

### Fixed
- The right-click menu no longer offers Unmount or Format on system and home
  partitions, including the EFI partition (credit @mendres82, PR #34)
- Drives mounted manually or via fstab no longer show an Unmount option that
  didn't actually work

---

## v0.7.11

### Internationalization
- Hungarian translation (credit @pelach, PR #42)
- Turkish translation completed, filling in previously untranslated strings
  (credit @TaylanTatli, PR #43)

---

## v0.7.10

### Internationalization
- Russian translation (credit @fish-dd, PR #40)
- German translation fixes for consistency (credit @crian, PR #39)

---

## v0.7.9

### Fixed
- Disk cards required a double-click to open even when Nautilus's "Single click
  to open items" preference was set; cards now follow the `click-policy` GSettings
  key live, the same way they already follow grid/list view mode (credit
  @MoshiurRahmanAdib, issue #28)

---

## v0.7.8

### Internationalization
- German translation (credit @mendres82, PR #32; credit @crian, PR #25)
- Korean translation (credit @Saintliy, PR #20)
- Turkish translation (credit @TaylanTatli, PR #36)
- Sidebar - Places strings (Home, Recent, Starred, Trash) added across Arabic, Catalan,
  Spanish, French, Italian, Korean, Portuguese, and Turkish, bringing every language file
  to full parity with the current string set
- `"Network Volumes"` renamed to `"Network"` across all language files to match the
  current sidebar/group naming

### Fixed
- German `msgid "Network"` previously fell back to the untranslated source string after
  the rename; now reads "Netzwerk"

---

## v0.7.7
Installer fixes for package-manager detection, plus more detailed system info.

### Fixed
- Installer picked `apt` over `dnf` on Fedora systems that have `apt`/`dpkg`
  installed for unrelated `.deb` tooling, causing the install to fail
  outright (credit @sam-eon, issue #27)
- openSUSE package name for the Nautilus Python bindings was outdated for
  current Tumbleweed (credit @mendres82, PR #33); installer now resolves the
  correct name across Tumbleweed, Leap, and future Python version bumps
- Installer could abort entirely on Linux distributions not explicitly listed
  in its detection table, instead of falling back to binary-presence detection
- Installer could silently overwrite its own `--version` argument when reading
  `/etc/os-release` on distributions that define a `VERSION` key

### Changed
- Installer's System section now shows the detected distribution name and
  version numbers for the package manager, `nautilus-python`, and `gettext`
- Sidebar Computer group now shows all places by default (Home, Recent, Starred,
  Network, Trash) instead of hiding Recent, Starred, and Network; users can still
  toggle visibility via settings

---

## v0.7.6
Icon-accuracy fixes for disk cards.

### Fixed
- Disc-group cards (mounted ISO images) now always show the optical disc icon
  instead of a generic removable-media icon, since `udisks` tags loop-mounted
  ISOs the same way it tags plain removable drives
- A drive icon wrapped in an emblem (e.g. a readonly/encrypted badge) with no
  resolvable base icon name no longer renders blank; it now falls back to the
  group's default icon

---

## v0.7.5
Fixes a grid-view column collapse caused by long disk/device names.

### Fixed
- A long disk or device name (e.g. an MTP device path) could inflate every card's
  width and collapse the grid view down to fewer columns than normal

### Changed
- List view now uses its own row layout, separate from the grid card: icon, name,
  free/total text, and a wider usage bar on a single full-width line

---

## v0.7.4
Small polish release for the Computer sidebar button.

### Fixed
- Computer sidebar button now stays selected while the Computer panel is open

### Changed
- Cleaned up internal sidebar code names and small maintenance details

---

## v0.7.3
Stability release: reworks sidebar place handling to rely on native Nautilus rows
wherever possible, removing a class of duplicate-row and flicker bugs.

### Changed
- Sidebar places (Home, Recent, Starred, Network, Trash) are no longer rebuilt as
  custom rows. They stay fully native (icons, tooltips, context menus, drag-and-drop,
  trash-full icon all maintained by Nautilus); sidebar-show-* settings now toggle the
  visibility of the native row directly
- Only the Computer row is still custom-built, in its own section above the native
  sidebar list

### Fixed
- Duplicate Trash (and other native place) rows could appear after Nautilus reordered
  its sidebar list (device mount/unmount, bookmark changes), caused by a positional
  CSS hide rule that did not survive reorders
- Computer row briefly disappearing during bookmark drag-and-drop
- Computer row incorrectly accepting file drops

---

## v0.7.2
### Docs
- Updated README and new content and screenshots/visuals

---

## v0.7.1
### Added
- Drag-and-drop visual feedback on sidebar places: rows that aren't valid drop destinations grey out while a file drag is over the sidebar, matching native Nautilus behaviour
- Home accepts file drops (copy/move via Nautilus's own FileOperations2 D-Bus, with native progress and undo)

### Fixed
- Custom sidebar places now grey out in sync with Nautilus's native rows during a drag, instead of only one group reacting

---

## v0.7.0
### Added
- Custom Location group on the sidebar, replacing Nautilus's native LOCATIONS section with a LOCATIONS-driven architecture
- Context menus on all sidebar locations (Computer, Home, Recent, Starred, Network, Trash): Open, Open in New Tab, Open in New Window, plus location-specific actions (Properties, File History Settings, Trash Settings, Empty Trash)
- Sidebar visibility settings: per-location toggles to show or hide each entry in the location group (Home, Recent, Starred, Network, Trash) (credit @Aeternitae, issue #18)

### UX
- Settings: "Visibility" group renamed to "Panel visibility" for clarity
- Settings: new "Sidebar visibility" group added after Panel visibility

## v0.6.0
### UX
- make Computer button on sidebar compatible with native style and custom GTK4 CSS styles
- make Computer view panel compatible with native style and custom GTK4 CSS styles


## v0.5.4
### i18n
- i18n: finalize all UI strings in the Computer view and Settings panel
- i18n: update and complete translations for all supported languages

---

## v0.5.3
### i18n
- Disk size units now use `GLib.format_size()` for locale-aware output (e.g. "Ko / Mo / Go" in French, "octet" for bytes) - no custom unit translations needed

---

## v0.5.2
### UX
- Reduced vertical spacing between group labels and their disk cards in the Computer view

### Fixed
- Computer sidebar row icon and label now align with native Nautilus rows (Home, Recent, etc.)

### Maintenance
- Sidebar design values (icon gap, row inset padding) moved to the centralized CSS block

---

## v0.5.1
### UX
- Settings page labels and descriptions improved across all sections
- Visibility section now includes a description explaining Visible, Merged, and Hidden
- "Show system partitions" toggle moved to the bottom of the Visibility group
- "Disk Usage Color" renamed to "Usage Bar Color" with a short description
- Group names simplified to location-style: Removable, Disc, Network (was: Removable Devices, Disc Images, Network Volumes)
- "On this Computer" removed from visibility controls - it is always visible as the merge target

---

## v0.5.0
### Added
- New "System" group separating root, boot, EFI, and swap from regular drives
- Per-group visibility control: each group can be Visible, Merged into "On this computer", or Hidden
- "Show system partitions" toggle to include boot and EFI entries in the System group (default off)
- Sort-by-type ordering in the merged "On this computer" view: System first, then local drives, then removable, disc, network
- `DiskGroup` dataclass encapsulating group logic and state

### Changed
- Five groups total: System, On this computer, Removable Devices, Disc Images, Network Volumes
- Settings: replaced `hide-system-partitions` with `show-system-partitions` (inverted, same default behavior)

### Fixed
- USB drives running a Linux system (iso9660 filesystem) now correctly appear in Removable Devices instead of Disc Images
- Loop-mounted ISO images continue to appear in Disc Images regardless of mount path

---

## v0.4.6
### Fixed
- Installer `--branch` and `--version` are now independent axes, not mutually exclusive
- Bad branch falls back to `main`, bad version falls back to latest tag - no hard errors
- Version resolution now uses git tags instead of GitHub Releases, so tags always resolve
- Install type section always shows `Source`, `Branch`, and `Version` lines
- Local installs show current branch and version from the local file, not arg values

## v0.4.5
### Fixed
- Installer fully POSIX compliant - removed all `local` keyword usage
- Installer uses CLI flags (`--version=`, `--branch=`) instead of env vars, which do not survive `curl | sh` pipes
- `--version` and `--branch` validated as mutually exclusive
- `--branch` probed early, fails hard on unknown branch name
- `apt` package detection uses `dpkg-query` to avoid false positives on partially-removed packages
- GitHub API fallback to `main` now prints a visible warning instead of silently proceeding
- Installer skips `sudo` when already running as root
- `--version`/`--branch` flags produce a warning on local installs instead of silently doing nothing

### Maintenance
- Extracted `SCHEMA_ID`, `GETTEXT_DOMAIN`, `PYCACHE_GLOB` constants - all derived names have a single source of truth

## v0.4.4
### Fixed
- Installer (`install.sh`) now POSIX `sh` compliant, fixes `curl | sh` failing on systems where `/bin/sh` is `dash` (e.g. Debian, Ubuntu)

## v0.4.3
### UX
- Fixed a brief flicker of the file view when navigating to Computer

## v0.4.2
### UX
- Panel now opens in ~20-65ms instead of ~500-600ms

## v0.4.1
### Internationalization
- Updated Arabic translations (credit @e6ad2020)
- Updated French translations

## v0.4.0
### Added
- Italian, Spanish, Catalan and Portuguese translations (credit @unaibenidorm)
- Non-interactive installer with `curl | sh`, `VERSION` and `BRANCH` env vars (credit @sour-source)

### Fixed
- Disk cards not updating when drives are connected or disconnected
- Disk cards not updating during active file transfers
- Level bar gradient not rendering on Ubuntu 22.04 LTS and other GTK 4.6.x systems
- Crash on startup when Nautilus opens directly to a folder (credit @e6ad2020, PR #10)
- Navigation crash on pathbar (credit @unaibenidorm, @e6ad2020, issue #11)

## v0.3.1
### Fixed
- Crash on startup when `~/Templates` is non-empty (issue #4)
- Level bar gradient direction incorrect in RTL languages (credit @e6ad2020)

### Internationalization
- Arabic translation for Disc Images group (credit @e6ad2020)

## v0.3.0
### Added
- Native Computer button in the left sidebar, replacing the bookmark approach
- Right-click context menu on the Computer sidebar button (Open, Open in New Tab, Open in New Window, Settings)
- Computer sidebar button highlights when Computer view is active

### Removed
- Bookmark-based sidebar entry and all related code

## v0.2.1
### Fixed
- Installer now aborts cleanly when a release is missing (credit @sour-source)
- Missing icon for mounted ISO images (credit @sour-source)

## v0.2.0
### Added
- Internationalization support (i18n)
- Arabic translations (credit @e6ad2020)
- French translations

### Fixed
- Nautilus inherits terminal locale on restart instead of GNOME session locale
