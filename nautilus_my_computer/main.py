"""Application state and Nautilus integration: MyComputerExtension itself.

This is the piece nautilus-my-computer.py (the hyphenated entry point Nautilus
loads directly) imports and re-exports. Everything else in this package is
stateless or takes `ext` as a parameter; this module is the one place that
holds GSettings handles, per-window state, and module-level caches.
"""

from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import threading
import time

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("GObject", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Nautilus, Pango

from nautilus_my_computer import bookmarks, file_view_menu, preferred_folders
from nautilus_my_computer.common import (
    _CARD_WIDTH,
    _DISK_CARD_ROW_SPACING,
    _DISK_CARD_SPACING,
    _FLOW_COLS_GRID,
    _FOLDER_CARD_ROW_SPACING,
    _FOLDER_CARD_SPACING,
    _FOLDER_FLOW_COLS_GRID,
    _,
    _all_widgets,
    _find_widget,
    _folder_card_width,
    _log,
    _pin_icon,
    _uri_is_hidden,
)
from nautilus_my_computer.preferred_folders import PreferredFolder
from nautilus_my_computer.widgets import (
    MyComputerCardSection,
    MyComputerContextualMenu,
    MyComputerDiskCard,
    MyComputerFolderCard,
    MyComputerMenuItem,
)


# ── Per-site injection toggles (debugging) ────────────────────────────────────
# We catch/inject into Nautilus at four independent sites. Each flag gates EVERY
# entry point for that site so a site can be fully isolated while debugging the
# Nautilus templates-menu use-after-free (crash on navigation with non-empty
# ~/Templates). Set to False to disable that site entirely. Env override:
# e.g. MC_MAIN_VIEW=0. Default all on.
def _flag(name: str, default: bool = True) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def _sidebar_mode(native_enabled_default: bool) -> str:
    # We inject only the Computer row into Nautilus's own native listbox and
    # leave every other place native (hiding rows the user toggled off). The old
    # inner/outer-wrapper modes that rebuilt a mimic places group are retired.
    mode = (os.environ.get("MC_SIDEBAR_MODE") or "").strip().lower()
    if mode in ("native-list", "native-list-bottom"):
        return mode
    return "native-list"


DEBUG_MAIN_VIEW_ACTIVE = _flag("MC_MAIN_VIEW")  # main view: panel overlay over the file view
DEBUG_COMPUTER_BUTTON_ACTIVE = _flag("MC_COMPUTER_BUTTON")  # left sidebar: "Computer" row injection
DEBUG_NATIVE_SIDEBAR_ACTIVE = _flag("MC_NATIVE_SIDEBAR")  # native sidebar row, set 0 for fallback
DEBUG_SIDEBAR_MODE = _sidebar_mode(
    DEBUG_NATIVE_SIDEBAR_ACTIVE
)  # inner-wrapper default | native-list | native-list-bottom | outer-wrapper
DEBUG_PATHBAR_ACTIVE = _flag("MC_PATHBAR")  # top URL bar: chip icon pinning
DEBUG_SORT_WATCH_ACTIVE = _flag("MC_SORT_WATCH")  # top view-mode/sort buttons: sort metadata watch
DEBUG_SELFTEST = _flag("MC_SELFTEST", default=False)  # in-process navigation self-test driver
DETACH_SETTINGS_WINDOW = False  # testing toggle: True opens settings as a standalone window

# ── Extension metadata (keep in sync with pyproject.toml) ────────────────────
EXT_NAME = "My Computer for Nautilus"
EXT_VERSION = "0.10.0"
EXT_AUTHOR = "Yann Masoch"
EXT_LICENSE = "MIT"
EXT_GITHUB = "https://github.com/yannmasoch/nautilus-my-computer"


DISKS_URI = "computer:///"
_DISKS_FILE = Gio.File.new_for_uri(DISKS_URI)
COMPUTER_LABEL = _("Computer")
COMPUTER_ICON = "computer-symbolic"  # icon used in sidebar and path bar
MENU_ITEM_LABEL = _("My Computer Settings")
PREFS_WIN_TITLE = _("My Computer Settings")
SCHEMA_ID = "io.github.yannmasoch.nautilus-my-computer"

VIEW_FILES = "files"  # visible_view token — files view (Overlay base)
VIEW_DISKINFO = "diskinfo"  # visible_view token — our panel (Overlay child)

METADATA_SORT_BY = "metadata::nautilus-icon-view-sort-by"
METADATA_SORT_REVERSED = "metadata::nautilus-icon-view-sort-reversed"

DBUS_FILE_MANAGER = "org.freedesktop.FileManager1"
DBUS_PATH_FILE_MANAGER = "/org/freedesktop/FileManager1"

# All updates are event-driven (VolumeMonitor signals, /proc/mounts POLLPRI,
# GSettings changed, Gio.FileMonitor, Gtk.Application window-added). The values
# below are one-shot retry/debounce intervals, not continuous poll periods.
_REFRESH_DEBOUNCE_MS = 300  # coalesce rapid mount/unmount/plug events
_WIN_INIT_RETRY_MS = 20  # retry interval while waiting for NautilusWindow widget tree
_WIN_INIT_MAX_ATTEMPTS = 100  # ~2 s budget waiting for the first view load to settle
_NAV_RETRY_MS = 60  # retry interval while navigating to computer:///
_TAB_WAIT_MS = 50  # retry interval while waiting for a new tab slot
_USAGE_GATE_MS = 1000  # idle cadence: try a statvfs sweep this often, skip while disk is busy
_USAGE_POLL_FAST_MS = 250  # fast cadence while writes are buffered (Dirty+Writeback elevated)
_USAGE_BUSY_RATIO = (
    0.50  # io_ticks delta / interval above this == disk busy → skip statvfs (avoid I/O contention)
)

_DIRTY_ACTIVE_THRESHOLD = (
    4 * 1000 * 1000
)  # /proc/meminfo Dirty+Writeback ≥ this → poll fast (above resting journal noise ~1–2 MB)
_USAGE_POLL_NETWORK_MS = 5000  # async D-Bus usage poll interval for GVfs/network mounts
_SORT_POLL_MS = 250  # gvfs sort-metadata poll cadence (only while header is hovered)
_STALE_RELEASE_FRAMES = 2  # keep detached panel generations alive across this many frame ticks


# Resolve the display name Nautilus shows in the title bar when at DISKS_URI,
# so panel detection works regardless of which URI is configured.
try:
    _info = Gio.File.new_for_uri(DISKS_URI).query_info(
        "standard::display-name", Gio.FileQueryInfoFlags.NONE, None
    )
    _LOCATION_TITLE = _info.get_display_name()
except Exception:
    _LOCATION_TITLE = COMPUTER_LABEL

# Localized title Nautilus shows when browsing the user's home folder.
# Used to distinguish a "default new window" (opened at Home) from a window
# that was explicitly opened to a specific folder.
_HOME_TITLE: str = GLib.dgettext("nautilus", "Home")

# Transient title Nautilus shows while a location is still loading. Treated as
# "window not settled yet" so it never consumes the start-on-computer one-shot.
_LOADING_TITLE: str = GLib.dgettext("nautilus", "Loading…")


def _is_unsettled_title(title: str) -> bool:
    """True while the window hasn't resolved to a real location yet."""
    return not title or title == _LOADING_TITLE


REAL_FSTYPES = {
    "ext4",
    "ext3",
    "ext2",
    "xfs",
    "btrfs",
    "f2fs",
    "ntfs",
    "ntfs3",
    "vfat",
    "exfat",
    "zfs",
    "reiserfs",
    "apfs",
    "erofs",
    "fuseblk",
}

NETWORK_FSTYPES = {
    "nfs",
    "nfs4",
    "cifs",
    "smb",
    "smb2",
    "smbfs",
    "fuse",
    "fuse.sshfs",
    "fuse.rclone",
    "fuse.s3fs",
    "fuse.davfs2",
    "davfs",
    "sshfs",
    "ftpfs",
    "gvfsd-fuse",
}

OPTICAL_FSTYPES = {"iso9660", "udf"}

# Mountpoint prefixes that indicate removable / external media
EXTERNAL_PREFIXES = ("/media/", "/run/media/", "/mnt/")

# Sidebar place URIs that never accept a file drop, mirroring Nautilus'
# check_valid_drop_target (recent:/// is hardcoded invalid) and its
# drag_open_exclusion_list. Used to grey native rows during a drag when the
# pointer is over our own listbox (Nautilus only dims its own rows while its
# list_box is hovered).
_SIDEBAR_DROP_EXCLUDED_URIS = frozenset(
    {
        "recent:///",
        "starred:///",
        DISKS_URI,
        "x-network-view:///",
    }
)


@dataclasses.dataclass
class PlaceEntry:
    """Describes one fixed sidebar place (Computer, Home, Recent, Starred, Network, Trash)."""

    name: str  # internal key ("computer", "home", ...)
    position: int  # visual order (0 = top)
    label: str  # display label (translatable)
    icon: str  # themed icon name
    uri: str  # location URI
    visible: bool = True
    tooltip: str = ""
    order_index: int = 0  # passed to NautilusSidebarRow "order-index" property
    menu: object = (
        None  # factory menu(ext, win, entry) -> MyComputerContextualMenu, or None for no menu
    )
    droppable: bool = False  # accepts file drops (copy/move destination)


def _open_actions_flat(ext, win, uri: str, open_enabled: bool = True) -> list:
    """Flat (non-submenu) open actions for the Computer sidebar row. The sidebar
    list is a fixed set of places, not folder-like cards, so unlike
    _open_actions() it keeps Open / Open in New Tab / Open in New Window as
    top-level items, matching GtkPlacesSidebar's native flat context menu. No
    shortcuts shown here, unlike the folder/disk card submenu."""
    return [
        MyComputerMenuItem(
            _("Open"),
            action=lambda: ext._do_open(uri, win),
            enabled=open_enabled,
        ),
        MyComputerMenuItem(
            _("Open in New Tab"),
            action=lambda: ext._do_open_tab(uri, win),
        ),
        MyComputerMenuItem(
            _("Open in New Window"),
            action=lambda: ext._do_open_window(uri),
        ),
    ]


def _computer_context_menu(ext, win, entry: PlaceEntry) -> MyComputerContextualMenu:
    """Computer row: open actions + settings, with Open greyed out when already shown."""
    uri = entry.uri
    on_computer = ext._windows.get(win, {}).get("visible_view") == VIEW_DISKINFO
    return MyComputerContextualMenu(
        _open_actions_flat(ext, win, uri, open_enabled=not on_computer)
        + [MyComputerMenuItem(MENU_ITEM_LABEL, action=lambda: ext._launch_prefs(win), section=1)]
    )


# PLACES holds only Computer: the one place we still build our own row for. It
# has no native equivalent, so it cannot be handled like the others below.
PLACES: list[PlaceEntry] = [
    PlaceEntry(
        name="my_computer",
        position=0,
        label=_LOCATION_TITLE,
        icon=COMPUTER_ICON,
        uri=DISKS_URI,
        tooltip=_("Open My Computer"),
        order_index=0,
        menu=_computer_context_menu,
    ),
]

# NATIVE_PLACES describes places that stay fully NATIVE - we never build rows for
# them. Only `name` (maps to a sidebar-show-* key), `uri` (matches the native row)
# and `label`/`icon` (for the Preferences toggle row) are used, by
# _apply_native_place_visibility and the sidebar-visibility prefs page. `visible`
# is the default on/off state. Kept as PlaceEntry (same structure as PLACES) in
# case a future place needs the full row-building fields again.
NATIVE_PLACES: list[PlaceEntry] = [
    PlaceEntry(
        name="home",
        position=1,
        label=_("Home"),
        icon="user-home-symbolic",
        uri=GLib.filename_to_uri(GLib.get_home_dir(), None),
    ),
    PlaceEntry(
        name="recent",
        position=2,
        label=_("Recent"),
        icon="document-open-recent-symbolic",
        uri="recent:///",
        visible=False,
    ),
    PlaceEntry(
        name="starred",
        position=3,
        label=_("Starred"),
        icon="starred-symbolic",
        uri="starred:///",
        visible=False,
    ),
    PlaceEntry(
        name="network",
        position=4,
        label=_("Network"),
        icon="network-computer-symbolic",
        uri="x-network-view:///",
        visible=False,
    ),
    PlaceEntry(
        name="trash",
        position=5,
        label=_("Trash"),
        icon="user-trash-symbolic",
        uri="trash:///",
    ),
]


# Maps each place name to its GSettings key controlling sidebar visibility.
# "my_computer" is intentionally absent -- it is always shown and has no toggle.
_PLACE_VISIBILITY_KEYS: dict[str, str] = {
    "home": "sidebar-show-home",
    "recent": "sidebar-show-recent",
    "starred": "sidebar-show-starred",
    "network": "sidebar-show-network",
    "trash": "sidebar-show-trash",
}


def _place_is_visible(entry: PlaceEntry, gsettings) -> bool:
    """Whether a place should appear in the custom sidebar group.

    Computer is always visible. Every other place is driven by its
    sidebar-show-* GSettings key, falling back to the static default.
    """
    gskey = _PLACE_VISIBILITY_KEYS.get(entry.name)
    if gskey is None:
        return True
    if gsettings is None:
        return entry.visible
    return gsettings.get_boolean(gskey)


def _disk_context_menu(ext, win, m) -> MyComputerContextualMenu:
    """Build a disk card's right-click menu from live mount state.

    Same three-section layout as before: open actions, then mount/eject/unmount +
    format (skipped for protected system/home mounts), then Properties (mounted
    only). Unmounted disks mount first, then open in the requested target.
    """
    nav_uri = m.nav_uri or (Gio.File.new_for_path(m.mountpoint).get_uri() if m.mountpoint else "")
    is_mounted = m.is_mounted
    device = m.device or ""
    if not device.startswith("/dev/") and m.gio_volume:
        unix_dev = m.gio_volume.get_identifier(Gio.VOLUME_IDENTIFIER_KIND_UNIX_DEVICE)
        if unix_dev:
            device = unix_dev

    # Section 0: open actions (all disks), collapsed into a single native "Open"
    # submenu. Mounted -> navigate; unmounted -> mount then open.
    if is_mounted and nav_uri:
        open_submenu = [
            MyComputerMenuItem(
                _("Open"), action=lambda: ext._do_open(nav_uri, win), shortcut="Return"
            ),
            MyComputerMenuItem(
                _("Open in New Tab"),
                action=lambda: ext._do_open_tab(nav_uri, win),
                shortcut="<Control>Return",
            ),
            MyComputerMenuItem(
                _("Open in New Window"),
                action=lambda: ext._do_open_window(nav_uri),
                shortcut="<Shift>Return",
            ),
        ]
        # See _do_open_with() / _open_actions() above. Only local mounts would ever
        # resolve in the system app chooser; network mounts (smb://, sftp://) have
        # no app handler, so omit it there entirely, like native Nautilus.
        if nav_uri.startswith("file://"):
            open_submenu.append(
                MyComputerMenuItem(
                    _("Open With…"), action=lambda: ext._do_open_with(nav_uri, win), section=1
                )
            )
    else:
        open_submenu = [
            MyComputerMenuItem(
                _("Open"),
                action=lambda: ext._do_mount_then_open(m, win, "current"),
                shortcut="Return",
            ),
            MyComputerMenuItem(
                _("Open in New Tab"),
                action=lambda: ext._do_mount_then_open(m, win, "tab"),
                shortcut="<Control>Return",
            ),
            MyComputerMenuItem(
                _("Open in New Window"),
                action=lambda: ext._do_mount_then_open(m, win, "window"),
                shortcut="<Shift>Return",
            ),
        ]
    items = [MyComputerMenuItem(_("Open"), submenu=open_submenu)]

    # Section 1: mount / unmount / eject + format (non-protected only).
    if not _is_protected_mount(m):
        if not is_mounted:
            if m.can_mount:
                items.append(
                    MyComputerMenuItem(_("Mount"), action=lambda: ext._do_mount(m, win), section=1)
                )
        elif m.can_eject:
            items.append(MyComputerMenuItem(_("Eject"), action=lambda: ext._do_eject(m), section=1))
        elif m.can_unmount:
            items.append(
                MyComputerMenuItem(_("Unmount"), action=lambda: ext._do_unmount(m), section=1)
            )
        if device.startswith("/dev/"):
            items.append(
                MyComputerMenuItem(_("Format…"), action=lambda: ext._do_format(device), section=1)
            )

    # Section 2: properties (mounted disks only).
    if is_mounted and nav_uri:
        items.append(
            MyComputerMenuItem(
                _("Properties"),
                action=lambda: ext._do_properties(nav_uri, win),
                shortcut="<Alt>Return",
                section=2,
            )
        )

    return MyComputerContextualMenu(items)


@dataclasses.dataclass
class MountInfo:
    """Typed representation of a single mounted/unmounted storage entry."""

    # Stable identity
    key: str  # "uuid:<uuid>" when UUID is known; otherwise device path or URI
    uuid: str | None  # filesystem UUID from /dev/disk/by-uuid (None for GVfs/unmounted)

    # Device info
    device: str  # /dev/sda1 or GVfs URI
    mountpoint: str  # local path or GVfs URI (empty for unmounted)
    fstype: str  # "ext4", "gvfs", "unmounted", "network-place", …
    opts: set  # mount options from /proc/mounts

    # Navigation
    nav_uri: str  # file:///… or smb://… (empty for unmounted)
    display_name: str  # user-facing label

    # Usage (updated by poll workers via dataclasses.replace)
    total: int
    free: int

    # GIO handles
    gio_icon: object | None = None
    gio_mount: object | None = None
    gio_volume: object | None = None

    # Flags
    is_gio: bool = False
    is_mounted: bool = True
    is_removable: bool = False
    can_eject: bool = False
    can_mount: bool = False
    can_unmount: bool = False
    is_network_place: bool = False
    is_hidden: bool = False  # standard::is-hidden on the mount root, local mounts only

    # Right-click menu factory menu(ext, win, m) -> MyComputerContextualMenu (built at show-time).
    menu: object = _disk_context_menu

    @property
    def used(self) -> int:
        return self.total - self.free

    @property
    def percent(self) -> float:
        return round(self.used / self.total * 100, 1) if self.total > 0 else 0.0


_MOUNT_ESCAPE_RE = re.compile(r"\\([0-7]{3})")


def _unescape_mount_field(s: str) -> str:
    """Decode octal escapes written by the kernel in /proc/mounts (space=\\040, etc.)."""
    return _MOUNT_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 8)), s)


def _read_os_name() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _is_ostree_booted() -> bool:
    """True on OSTree/image-based systems, including bootc distributions."""
    return os.path.exists("/run/ostree-booted")


def _is_ostree_implementation_mount(mountpoint: str) -> bool:
    """True for implementation mounts that should not be shown as drives."""
    if not _is_ostree_booted():
        return False
    return mountpoint in ("/etc", "/var", "/sysroot") or mountpoint.startswith("/sysroot/")


def _statvfs_usage(path: str) -> tuple[int, int] | None:
    """Return total/free bytes for a path, or None when unavailable."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    return st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize


def _root_usage() -> tuple[int, int] | None:
    """Return user-meaningful root capacity.

    On OSTree/bootc systems, / may be the small immutable image view. Prefer
    the writable/backing deployment filesystem for the displayed root card
    while still navigating to /.
    """
    if _is_ostree_booted():
        candidates = [_statvfs_usage(path) for path in ("/var", "/sysroot") if os.path.exists(path)]
        candidates = [usage for usage in candidates if usage is not None]
        if candidates:
            return max(candidates, key=lambda usage: usage[0])
    return _statvfs_usage("/")


def _root_mount_info() -> MountInfo | None:
    """Build a canonical root entry when /proc/mounts does not expose one cleanly."""
    usage = _root_usage()
    if usage is None:
        return None
    total, free = usage
    return MountInfo(
        key="path:/",
        uuid=None,
        device="/",
        mountpoint="/",
        fstype="rootfs",
        opts=set(),
        total=total,
        free=free,
        display_name=_read_os_name() or "/",
        nav_uri=Gio.File.new_for_path("/").get_uri(),
    )


def _build_uuid_map() -> dict[str, str]:
    """Return {real_device_path: uuid_string} from /dev/disk/by-uuid."""
    result: dict[str, str] = {}
    by_uuid = "/dev/disk/by-uuid"
    if not os.path.isdir(by_uuid):
        return result
    try:
        for entry in os.scandir(by_uuid):
            if entry.is_symlink():
                try:
                    result[os.path.realpath(entry.path)] = entry.name
                except OSError:
                    pass
    except OSError:
        pass
    return result


def _is_system_mount(m: MountInfo) -> bool:
    """True for root, boot, EFI, and swap - mounts that belong to the System group."""
    return (
        m.mountpoint == "/" or m.mountpoint in ("/boot", "/boot/efi", "/efi") or m.fstype == "swap"
    )


def _is_protected_mount(m: MountInfo) -> bool:
    """True if Unmount/Eject/Format should be hidden for this mount.

    Used only for context-menu action gating, not display grouping - unlike
    _is_system_mount, a protected mount may still appear under "On this Computer".
    Backed by Gio.unix_mount_is_system_internal(), the same heuristic GNOME uses,
    so it covers most system mounts across distros without a hardcoded list. Two
    cases it can miss, kept as an explicit fallback: a per-user /home/<user> mount
    (e.g. encrypted home) which the signal doesn't flag but is still home; and the
    EFI System Partition, which some distros mount without marking it internal.
    """
    if m.is_gio or not m.mountpoint.startswith("/"):
        return False
    if m.mountpoint == "/home" or m.mountpoint.startswith("/home/"):
        return True
    if m.mountpoint in ("/boot/efi", "/efi"):
        return True
    entry = Gio.unix_mount_at(m.mountpoint)
    if isinstance(entry, tuple):
        entry = entry[0]
    return bool(entry and Gio.unix_mount_is_system_internal(entry))


def _classify_mount(m: MountInfo) -> str:
    """Return 'system', 'local', 'removable', 'disc', or 'network' for a mount entry."""
    # Unmounted volumes are never part of the running system.
    # Removable (USB, optical) -> "Removable"; others -> "On this Computer"
    if not m.is_mounted:
        return "removable" if m.is_removable else "local"

    # GVfs mounts -- phones/cameras (MTP, PTP) go to removable; rest are network
    if m.is_gio:
        if m.nav_uri.startswith(("mtp://", "gphoto2://", "afc://", "obex://")):
            return "removable"
        return "network"

    # Removable-media paths: check path before fstype so USB drives (including live Linux
    # USBs with iso9660 partitions) are not misclassified as discs. Exception: loop-mounted
    # ISO images also land under /run/media/ but their device is /dev/loopN -- those are discs.
    if any(m.mountpoint.startswith(p) for p in EXTERNAL_PREFIXES):
        if m.fstype in OPTICAL_FSTYPES and m.device.startswith("/dev/loop"):
            return "disc"
        return "removable" if m.is_removable else "local"

    # Optical filesystems not under external paths -> physical disc or image
    if m.fstype in OPTICAL_FSTYPES:
        return "disc"

    # x-gvfs-show fstab entries and known network fstypes -> network
    if "x-gvfs-show" in m.opts or m.fstype in NETWORK_FSTYPES or m.fstype.startswith("fuse"):
        return "network"

    # Root, boot/EFI, swap -> System group
    if _is_system_mount(m):
        return "system"

    return "local"


def _get_local_mount_tier(m: MountInfo) -> tuple[int, str]:
    """Return (tier, name) for hierarchical sorting within 'local' group.
    Tier: 0=root, 1=system partitions, 2=mounted, 3=unmounted
    Used by 'sort by type' mode."""
    name = (m.display_name or "").lower()
    if m.mountpoint == "/":
        return (0, name)
    if m.mountpoint in ("/boot", "/boot/efi", "/efi") or m.fstype == "swap":
        return (1, name)
    if m.is_mounted:
        return (2, name)
    return (3, name)


# Ordered group spec: (key, display_label, gsettings_key)
# "local" is the merge target for other groups -- always visible, no gsettings key
_GROUP_SPEC: list[tuple[str, str, str | None]] = [
    ("system", "System", "visibility-system"),
    ("local", "On this Computer", None),
    ("removable", "Removable", "visibility-removable"),
    ("disc", "Disc", "visibility-disc"),
    ("network", "Network", "visibility-network"),
]


@dataclasses.dataclass
class PanelGroup:
    """A rendered group on the Computer view: a heading + a grid/list of cards.

    kind selects the card builder used in _populate(): "disk" for MountInfo
    items (the existing disk groups), "folder" for PreferredFolder items.
    """

    key: str
    label: str
    visible: bool = True
    merged: bool = False
    kind: str = "disk"
    items: list = dataclasses.field(default_factory=list)

    def add_item(self, m) -> None:
        self.items.append(m)

    def sort_items(self, key_func, reverse: bool = False) -> None:
        self.items.sort(key=key_func, reverse=reverse)


_disk_data: dict[str, MountInfo] = {}
_folder_data: dict[str, "PreferredFolder"] = {}
_network_places: list[MountInfo] = []  # populated async from network:///

_CSS = b"""
* {
    /* Mirrors Nautilus's own --accent-bg-color override from its bundled style.css
       (.nautilus-grid-view gridview rule). Theme-safe: GTK themes load at priority
       200 (THEME), this loads at 600 (APPLICATION) - themes cannot override it.
       Only user stylesheets at priority 800 (USER) can, which is correct behavior. */
    --diskinfo-selection-grey: #959595;
}
.diskinfo-panel {
}
.diskinfo-panel flowbox {
    --accent-bg-color: var(--diskinfo-selection-grey);
    padding: 0;
    margin: 0;
}
.mc-icon-grid {
    --accent-bg-color: var(--diskinfo-selection-grey);
}
.diskinfo-subtext {
    color: @insensitive_fg_color;
}
.unmounted {
    opacity: 0.5;
}
.vanilla-diskinfo-view-hidden > * {
    opacity: 0;
}
/* For testing/debugging: shows injected panel outline vs native sidebar. */
.debug {
    background: red;
}
.debug-gap {
    margin: 0;
    padding: 0;
}
/* Zero the theme's default flowboxchild padding/margin so all card spacing is
   controlled by our own widgets (col/row spacing on the FlowBox, margins on
   the card itself) instead of fighting the theme's built-in wrapper inset. */
.diskinfo-panel flowboxchild {
    padding: 0;
    margin: 0;
}
/* Folder cards already show the reorder via live drag-move (see
   _wire_reorder_preview), so the native drop-target border is redundant.
   Mirrors Nautilus's own .nautilus-list-view .nautilus-view-cell:drop(active)
   reset (style.css), just scoped to our panel's grid cells instead. */
.diskinfo-panel .nautilus-view-cell:drop(active) {
    box-shadow: none;
}
/* Folder cards own their gutters via widget spacing/FlowBox spacing, so strip
   Nautilus's native cell inset from that card class only. */
.diskinfo-panel .mc-folder-card {
    padding: 0;
    margin: 0;
}
/* Reusable highlight for any card type. Applied programmatically (e.g. on
   the dragged folder card during reorder) to show the current landing slot.
   alpha(@window_fg_color, 0.07) is Adwaita's hover overlay: subtle dark tint
   in light mode, subtle white tint in dark mode -- matches the hover bg on
   activatable grid/list rows. Border-radius matches .nautilus-view-cell (12px). */
.mc-selected {
    background-color: alpha(@window_fg_color, 0.07);
    border-radius: 12px;
}
"""

# Seam between the separate My Computer listbox and Nautilus' native list
# directly below it. Both carry .navigation-sidebar (theme base padding 6px); the
# + combinator zeroes the touching edges so the two lists read as one column.
_CSS_SIDEBAR = b"""
#sidebar_my_computer_listbox.navigation-sidebar {
    padding-bottom: 0;
}
#sidebar_my_computer_listbox.navigation-sidebar + .navigation-sidebar {
    padding-top: 0;
}
"""


def _apply_native_place_visibility(native_listbox: Gtk.ListBox, gsettings) -> None:
    """Show/hide native sidebar place rows per the user's sidebar-show-* settings.

    We do NOT mimic native rows anymore. Home/Recent/Starred/Network/Trash stay
    fully native (icons, tooltips, context menus, drag-and-drop, trash-full icon -
    all maintained by Nautilus). The only feature we add over them is a per-place
    on/off toggle, which is just selectively hiding the native row:

        sidebar-show-<place> == True  -> native row visible (untouched, native)
        sidebar-show-<place> == False -> native row hidden

    Computer has no native row (we inject our own), so it is not handled here.

    Matched by URI (not position) and applied with `set_visible()` on the row
    widget, so the state follows the row when Nautilus reorders the list (device
    mount/unmount, bookmark add/remove, async populate). A positional nth-child
    CSS rule did NOT survive reorders. We only ever touch rows whose URI is one of
    our places; Nautilus's own placeholder rows (e.g. the empty "Add a new
    bookmark" drop target) are never forced visible.

    Safe to call repeatedly; re-armed on every native list change via
    `observe_children()` items-changed (see _watch_native_list_changes)."""
    # uri -> should-be-visible, for the togglable native places.
    want_visible = {p.uri: _place_is_visible(p, gsettings) for p in NATIVE_PLACES}
    hidden = 0
    idx = 0
    while (row := native_listbox.get_row_at_index(idx)) is not None:
        try:
            uri = row.get_property("uri")
        except Exception:
            uri = None
        if uri in want_visible:
            visible = want_visible[uri]
            if row.get_visible() != visible:
                row.set_visible(visible)
            if not visible:
                hidden += 1
        idx += 1
    _log(f"_apply_native_place_visibility: {hidden} native place row(s) hidden by setting")


def _read_io_busy() -> tuple:
    """Return (io_ticks_ms, ios_in_progress) summed over physical block devices.

    Reads /proc/diskstats — a pure procfs read with no filesystem/journal
    involvement, so unlike statvfs it never blocks or contends with an in-flight
    file operation. Used purely as a disk-busy gate: while the disk has I/O in
    flight we must NOT call statvfs (statvfs blocks for seconds under ext4 journal
    load and contends with the very operation in progress — confirmed cause of
    sluggish copy/delete when the panel was visible). io_ticks counts wall-time the
    device had at least one request in flight; its delta over an interval gives the
    busy fraction. ios_in_progress is the instantaneous queue depth.

    Note: this is NOT the previously-removed diskstats *estimation* approach — we
    never derive free space from it, only gate when it is safe to call statvfs."""
    ticks = inflight = 0
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                p = line.split()
                if len(p) < 14:
                    continue
                name = p[2]
                if name.startswith(("loop", "ram", "zram", "dm-", "sr")):
                    continue
                try:
                    inflight += int(p[11])  # field 12: I/Os currently in progress
                    ticks += int(p[12])  # field 13: ms spent doing I/Os (io_ticks)
                except ValueError:
                    continue
    except OSError:
        pass
    return ticks, inflight


def _read_dirty_bytes() -> int:
    """Return Dirty + Writeback bytes from /proc/meminfo (a pure procfs read).

    This is the one *forward* signal for an in-progress file operation: it rises
    while writes are buffered in the page cache, *before* the kernel flushes them
    to the device (the moment statvfs/diskstats finally change). It is used ONLY
    as a cadence hint — poll faster while it is elevated, and force one definitive
    sweep when it drains (the flush). It is global (not per-device), so it must
    NEVER be used to estimate or display free space — only to time statvfs."""
    dirty = writeback = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("Dirty:"):
                    dirty = int(line.split()[1]) * 1024  # reported in KiB
                elif line.startswith("Writeback:"):
                    writeback = int(line.split()[1]) * 1024
                    break  # Writeback follows Dirty in /proc/meminfo; both seen
    except (OSError, ValueError, IndexError):
        pass
    return dirty + writeback


def _get_gsettings() -> Gio.Settings | None:
    try:
        return Gio.Settings.new(SCHEMA_ID)
    except Exception:
        return None


def _scan_mounts(show_system_partitions: bool = False) -> list[MountInfo]:
    mounts: list[MountInfo] = []
    seen: set[str] = set()
    uuid_map = _build_uuid_map()
    has_root = False

    # Build mountpoint → Gio.Icon / Gio.Mount from VolumeMonitor so we can
    # attach the real hardware icon and GIO handle to each /proc/mounts entry.
    # Also build a UUID fallback for mounts whose root path doesn't match the
    # /proc/mounts mountpoint (e.g. root on LUKS/dm-crypt).
    icon_by_path: dict[str, Gio.Icon] = {}
    mount_by_path: dict[str, object] = {}
    mount_by_uuid: dict[str, object] = {}
    try:
        vm = Gio.VolumeMonitor.get()
        for gm in vm.get_mounts():
            root = gm.get_root()
            path = root.get_path()
            if path:
                icon_by_path[path] = gm.get_icon()
                mount_by_path[path] = gm
            vol = gm.get_volume()
            if vol:
                uid = vol.get_identifier(Gio.VOLUME_IDENTIFIER_KIND_UUID)
                if uid:
                    mount_by_uuid[uid] = gm
    except Exception:
        pass

    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                device = _unescape_mount_field(parts[0])
                mountpoint = _unescape_mount_field(parts[1])
                fstype, options = parts[2], parts[3]
                opts = set(options.split(","))
                if _is_ostree_implementation_mount(mountpoint):
                    continue
                gvfs_show = "x-gvfs-show" in opts
                is_external = any(mountpoint.startswith(p) for p in EXTERNAL_PREFIXES)
                if (
                    fstype not in REAL_FSTYPES
                    and not gvfs_show
                    and not is_external
                    and mountpoint != "/"
                ) or device in seen:
                    continue
                if not show_system_partitions and mountpoint in ("/boot", "/boot/efi", "/efi"):
                    continue
                seen.add(device)
                try:
                    usage = _root_usage() if mountpoint == "/" else _statvfs_usage(mountpoint)
                    if usage is None:
                        continue
                    total, free = usage
                    real_dev = os.path.realpath(device)
                    uuid = uuid_map.get(real_dev)
                    gio_mount = mount_by_path.get(mountpoint) or (
                        mount_by_uuid.get(uuid) if uuid else None
                    )
                    name = (
                        (gio_mount.get_name() if gio_mount else None)
                        or (mountpoint == "/" and _read_os_name())
                        or os.path.basename(mountpoint)
                        or "/"
                    )
                    gio_volume = gio_mount.get_volume() if gio_mount else None
                    gio_drive = gio_volume.get_drive() if gio_volume else None
                    key = f"uuid:{uuid}" if uuid else device
                    if mountpoint == "/":
                        has_root = True
                    nav_uri = Gio.File.new_for_path(mountpoint).get_uri()
                    mounts.append(
                        MountInfo(
                            key=key,
                            uuid=uuid,
                            device=device,
                            mountpoint=mountpoint,
                            fstype=fstype,
                            opts=opts,
                            total=total,
                            free=free,
                            display_name=name,
                            nav_uri=nav_uri,
                            is_hidden=_uri_is_hidden(nav_uri),
                            gio_icon=icon_by_path.get(mountpoint),
                            gio_mount=gio_mount,
                            gio_volume=gio_volume,
                            is_removable=gio_drive.is_removable() if gio_drive else False,
                            can_eject=bool(
                                (gio_volume and gio_volume.can_eject())
                                or (gio_mount and gio_mount.can_eject())
                                or (gio_drive and gio_drive.can_eject())
                            ),
                            can_unmount=bool(gio_mount and gio_mount.can_unmount()),
                        )
                    )
                except OSError:
                    pass
    except OSError:
        pass
    if not has_root:
        root = _root_mount_info()
        if root is not None:
            mounts.insert(0, root)
    return mounts


def _scan_gio_mounts() -> list[MountInfo]:
    """Enumerate GVfs/network mounts via Gio.VolumeMonitor.

    Returns mounts that are NOT file:// (those are already covered by
    _scan_mounts via /proc/mounts), e.g. smb://, sftp://, mtp://, dav://.
    """
    results: list[MountInfo] = []
    try:
        vm = Gio.VolumeMonitor.get()
        for mount in vm.get_mounts():
            root = mount.get_root()
            uri = root.get_uri()

            # Skip regular local filesystems — already in /proc/mounts
            if uri.startswith("file://"):
                continue
            # Skip virtual/meta locations
            if uri.startswith(("trash://", "recent://", "burn://")):
                continue

            name = mount.get_name() or uri
            local_path = root.get_path()  # FUSE path, may be None

            total = free = 0
            if local_path:
                try:
                    st = os.statvfs(local_path)
                    total = st.f_blocks * st.f_frsize
                    free = st.f_bavail * st.f_frsize
                except OSError:
                    pass

            gio_volume = mount.get_volume()
            gio_drive = gio_volume.get_drive() if gio_volume else None
            # Only stat via the local FUSE path -- query_info() on the bare GVfs URI
            # would hit the network synchronously and could block this scan.
            is_hidden = (
                _uri_is_hidden(Gio.File.new_for_path(local_path).get_uri()) if local_path else False
            )
            results.append(
                MountInfo(
                    key=uri,
                    uuid=None,
                    device=uri,
                    mountpoint=local_path or uri,
                    fstype="gvfs",
                    opts=set(),
                    total=total,
                    free=free,
                    display_name=name,
                    nav_uri=uri,
                    is_hidden=is_hidden,
                    is_gio=True,
                    gio_icon=mount.get_icon(),
                    gio_mount=mount,
                    gio_volume=gio_volume,
                    is_removable=gio_drive.is_removable() if gio_drive else False,
                    can_eject=bool(
                        (gio_volume and gio_volume.can_eject())
                        or mount.can_eject()
                        or (gio_drive and gio_drive.can_eject())
                    ),
                    can_unmount=bool(mount.can_unmount()),
                )
            )
    except Exception:
        pass
    return results


def _scan_gio_volumes() -> list[MountInfo]:
    """Enumerate Gio volumes that are connected but not yet mounted.

    Volumes already mounted are covered by _scan_mounts / _scan_gio_mounts,
    so we skip them here to avoid duplicates.
    """
    results: list[MountInfo] = []
    try:
        vm = Gio.VolumeMonitor.get()
        for volume in vm.get_volumes():
            if volume.get_mount() is not None:
                continue  # already mounted — covered elsewhere
            name = volume.get_name() or "Unknown Device"
            drive = volume.get_drive()
            is_removable = drive.is_removable() if drive else True
            results.append(
                MountInfo(
                    key=f"vol:{name}",
                    uuid=None,
                    device=f"vol:{name}",
                    mountpoint="",
                    fstype="unmounted",
                    opts=set(),
                    total=0,
                    free=0,
                    display_name=name,
                    nav_uri="",
                    is_mounted=False,
                    is_removable=is_removable,
                    gio_icon=volume.get_icon(),
                    gio_volume=volume,
                    can_eject=bool(volume.can_eject() or (drive and drive.can_eject())),
                    can_mount=bool(volume.can_mount()),
                )
            )
    except Exception:
        pass
    return results


def _refresh_network_places(on_done=None) -> None:
    """Enumerate network:/// in a background thread.

    GVfs returns both recent ("Previous") and discovered ("Available on
    Current Network") entries.  Calls on_done() on the main thread when
    finished so the caller can repopulate the view.
    """

    def _worker():
        global _network_places
        results: list[MountInfo] = []
        try:
            gfile = Gio.File.new_for_uri("network:///")
            enumerator = gfile.enumerate_children(
                "standard::name,standard::display-name,standard::icon,standard::target-uri",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
            while True:
                info = enumerator.next_file(None)
                if info is None:
                    break
                name = info.get_display_name() or info.get_name()
                icon = info.get_icon()
                target = info.get_attribute_string("standard::target-uri") or ""
                nav_uri = target or gfile.get_child(info.get_name()).get_uri()
                if not nav_uri or nav_uri.startswith("network:///"):
                    if not target:
                        continue
                results.append(
                    MountInfo(
                        key=f"netplace:{nav_uri}",
                        uuid=None,
                        device=nav_uri,
                        mountpoint=nav_uri,
                        fstype="network-place",
                        opts=set(),
                        total=0,
                        free=0,
                        display_name=name,
                        nav_uri=nav_uri,
                        gio_icon=icon,
                        is_network_place=True,
                    )
                )
            enumerator.close(None)
        except Exception as e:
            _log(f"network:/// enumerate: {e}")
        _network_places = results
        if on_done:
            GLib.idle_add(on_done)

    threading.Thread(target=_worker, daemon=True).start()


def _refresh(mounts: list[MountInfo]) -> bool:
    global _disk_data
    new_data = {m.key: m for m in mounts}
    changed = new_data != _disk_data
    _disk_data = new_data
    return changed


def _window_is_at_disks(win) -> bool:
    """True if the window's active slot is currently showing DISKS_URI.

    Reads the NautilusWindowSlot "location" GFile property on demand. No
    persistent signal, no set_child (safe re: issue #11). Prefers the active
    slot so tabs are handled; falls back to the first slot with a location.
    """
    fallback = None
    for w in _all_widgets(win):
        if "Slot" not in type(w).__name__:
            continue
        try:
            loc = w.get_property("location")
        except TypeError:
            continue
        if loc is None:
            continue
        try:
            if w.get_property("active"):
                return loc.equal(_DISKS_FILE)
        except TypeError:
            pass
        fallback = loc
    return fallback is not None and fallback.equal(_DISKS_FILE)


def _is_nautilus_window(win: Gtk.Window) -> bool:
    """Identify a Nautilus application window by layered fallback.

    Tier 1: buildable_id == 'NautilusWindow'
    Tier 2: class name  == 'NautilusWindow'
    Tier 3: css class      'nautilus-window'
    Tier 4: structural  — contains Adw.OverlaySplitView
    """
    bid = win.get_buildable_id() if hasattr(win, "get_buildable_id") else None
    if bid and bid == "NautilusWindow":
        return True
    if type(win).__name__ == "NautilusWindow":
        if bid != "NautilusWindow":
            _log("is_nautilus_window: matched via class_name (buildable_id drift)")
        return True
    if hasattr(win, "has_css_class") and win.has_css_class("nautilus-window"):
        _log("is_nautilus_window: matched via css class (class/id drift)")
        return True
    if any(isinstance(w, Adw.OverlaySplitView) for w in _all_widgets(win)):
        _log("is_nautilus_window: matched via structural navigation (significant drift)")
        return True
    return False


class MyComputerExtension(GObject.GObject, Nautilus.MenuProvider):
    def __init__(self):
        super().__init__()
        # Maps each NautilusWindow to its per-window state dict:
        #   overlay, panel, content_box, force_disks, initial_title
        self._windows: dict = {}
        self._polling_started = False
        self._refresh_pending = False  # debounce flag for live-refresh
        self._local_poll_stop: threading.Event | None = None
        self._net_poll_timer_id: int | None = None
        self._net_poll_cancellable: Gio.Cancellable | None = None
        self._folder_refresh_cancellable = Gio.Cancellable()
        self._folder_monitors: dict[str, Gio.FileMonitor] = {}  # keyed by parent dir URI
        self._watched_folder_keys: set[str] = set()
        self._last_selected_folder_uri: str | None = None  # see get_file_items()

        self._sort_column: str = "name"
        self._sort_reverse: bool = False
        self._view_mode: str = "icon-view"
        self._click_policy: str = "double"  # Nautilus "click-policy": 'single' or 'double'
        # Sort is read from per-folder GVfs metadata. There is no usable event
        # for it (the metadata daemon writes via mmap so file monitors never
        # fire, and the GTK4 Python bindings don't expose get_action_group, so
        # we can't subscribe to Nautilus's "view.sort" GAction). We therefore
        # poll — but only while the pointer is over the header bar (where the
        # sort menu lives) and the Computer panel is visible.
        # _sort_hover tracks whether the pointer is currently inside the navbar.
        # The poll arms on enter and disarms on leave, with a short grace period
        # to cover the gap when the pointer moves from the navbar into the sort
        # popover (which is a separate native surface and triggers a leave event).
        self._sort_poll_id = None  # GLib source id while polling, else None
        self._sort_hover = False  # True while pointer is inside the navbar
        self._nautilus_prefs = None  # Gio.Settings for org.gnome.nautilus.preferences
        self._bar_css_provider = Gtk.CssProvider()
        self._bar_css_display = None

        self._gsettings = _get_gsettings()
        if self._gsettings:
            self._start_on_disks: bool = self._gsettings.get_boolean("start-on-disks")
            self._gsettings.connect("changed", self._on_settings_changed)
        else:
            self._start_on_disks = False

        _show_sys_parts = (
            self._gsettings.get_boolean("show-system-partitions") if self._gsettings else False
        )
        _refresh(_scan_mounts(_show_sys_parts) + _scan_gio_mounts() + _scan_gio_volumes())

        # Watch /proc/mounts at the kernel level — POLLPRI fires on any
        # mount/unmount regardless of how it happened (udisks, manual, FUSE…)
        try:
            self._mounts_file = open("/proc/mounts", "r")
            GLib.io_add_watch(
                self._mounts_file,
                GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.ERR | GLib.IOCondition.PRI,
                self._on_proc_mounts_changed,
            )
        except OSError:
            self._mounts_file = None

        # VolumeMonitor signals — catch drive plug/unplug and GVfs events
        self._volume_monitor = Gio.VolumeMonitor.get()
        for sig in (
            "mount-added",
            "mount-removed",
            "volume-added",
            "volume-removed",
            "drive-connected",
            "drive-disconnected",
            "drive-changed",
        ):
            self._volume_monitor.connect(sig, self._on_disk_event)

        # Kick off async network:/// discovery immediately
        _refresh_network_places(on_done=self._do_live_refresh)

        GLib.idle_add(self._late_init)

    # ── Initialisation ────────────────────────────────────────────────────────

    def _late_init(self) -> bool:
        # Catch any windows that already existed before we connected signals.
        self._check_new_windows()

        if not self._polling_started:
            self._polling_started = True
            # Instant detection of new windows via signal (no polling needed).
            app = Gtk.Application.get_default()
            if app:
                app.connect("window-added", self._on_window_added)
            self._read_sort_metadata()
            self._read_view_mode()
            self._watch_view_mode()

        return False

    def _on_window_added(self, _app, win: Gtk.Window) -> None:
        """Instant handler for new Nautilus windows — defers injection until load settles."""
        self._schedule_window_init(win)

    def _schedule_window_init(self, win: Gtk.Window) -> None:
        """Wait for the window's first view load to settle, then inject on a
        low-priority idle.

        Injecting our Gtk.Overlay reparents the AdwToolbarView content. Doing that
        during Nautilus's files_view_begin_loading races with its templates
        context-menu rebuild: with a non-empty ~/Templates,
        slot_on_templates_menu_changed rebuilds a GtkPopoverMenu whose internal
        GtkStack our tree surgery destabilises, hitting a Nautilus-core
        use-after-free that segfaults on GTK 4.22 / GNOME 50 (GTK_IS_STACK
        assertion → SIGSEGV). Deferring until the load has finished, and running
        the injection at PRIORITY_LOW (after Nautilus's loading idles drain),
        removes the overlap. See issue #4. Empty ~/Templates never triggers it.
        """
        if not _is_nautilus_window(win) or win in self._windows:
            return
        attempts = [0]

        def _try() -> bool:
            if win in self._windows:
                return GLib.SOURCE_REMOVE
            attempts[0] += 1
            # Hold off until the first load has settled (title resolved to a
            # real location, not "Loading…"). Measured: title-settle is the
            # latest of the real readiness signals (tree/mapped/title), lagging
            # by ~20-40ms — typically settling within ~20-65ms of window-added.
            # No fixed floor: PRIORITY_LOW on the injection idle (below) is what
            # actually avoids the issue #4 templates-menu race, not extra delay.
            if _is_unsettled_title(win.get_title() or ""):
                if attempts[0] > _WIN_INIT_MAX_ATTEMPTS:
                    # Window never settled (rare) — inject anyway so the
                    # extension still works; route through the low-prio idle.
                    GLib.idle_add(self._deferred_init_window, win, priority=GLib.PRIORITY_LOW)
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            GLib.idle_add(self._deferred_init_window, win, priority=GLib.PRIORITY_LOW)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(_WIN_INIT_RETRY_MS, _try)

    def _deferred_init_window(self, win: Gtk.Window) -> bool:
        """Low-priority idle wrapper around _init_window (always one-shot)."""
        if win not in self._windows and _is_nautilus_window(win):
            self._init_window(win)
        return GLib.SOURCE_REMOVE

    def _init_window(self, win: Gtk.Window) -> bool:
        css = Gtk.CssProvider()
        css.load_from_data(_CSS)
        display = win.get_display()
        Gtk.StyleContext.add_provider_for_display(
            display,
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        css_sidebar = Gtk.CssProvider()
        css_sidebar.load_from_data(_CSS_SIDEBAR)
        Gtk.StyleContext.add_provider_for_display(
            display,
            css_sidebar,
            Gtk.STYLE_PROVIDER_PRIORITY_USER + 1,
        )
        if self._bar_css_display is None:
            self._bar_css_display = display
            self._apply_bar_color()
        if self._inject_overlay(win):
            win.connect("destroy", self._on_window_destroyed)
            win.connect("notify::title", self._on_title_changed)

            if DEBUG_COMPUTER_BUTTON_ACTIVE:
                self._inject_sidebar_link(win)
            self._attach_pathbar_menu_watch(win)
            self._attach_file_view_context_menu(win)
            self._on_title_changed(win, None)

            if DEBUG_SELFTEST and not getattr(self, "_selftest_started", False):
                self._selftest_started = True
                GLib.timeout_add(3000, lambda: self._run_selftest(win))

            return True
        return False

    def _run_selftest(self, win) -> bool:
        """Debug-only: drive in-process navigation (no keyboard/focus needed) so
        the templates-menu crash can be reproduced deterministically."""
        home = os.path.expanduser
        steps = [
            DISKS_URI,
            Gio.File.new_for_path(home("~/Downloads")).get_uri(),
            DISKS_URI,
            Gio.File.new_for_path(home("~/Documents")).get_uri(),
            DISKS_URI,
            Gio.File.new_for_path(home("~/Downloads")).get_uri(),
        ]
        idx = [0]

        def step():
            if win not in self._windows:
                _log("SELFTEST: window gone")
                return GLib.SOURCE_REMOVE
            if idx[0] >= len(steps):
                _log("SELFTEST DONE: survived all navigations")
                return GLib.SOURCE_REMOVE
            uri = steps[idx[0]]
            idx[0] += 1
            _log(f"SELFTEST step -> {uri}")
            for w in _all_widgets(win):
                if "Slot" in type(w).__name__:
                    try:
                        if w.activate_action("open-location", GLib.Variant("s", uri)):
                            break
                    except Exception:
                        pass
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(2500, step)
        return GLib.SOURCE_REMOVE

    def _check_new_windows(self) -> bool:
        toplevels = Gtk.Window.list_toplevels()
        found_any = False
        for win in toplevels:
            if _is_nautilus_window(win) and win not in self._windows:
                found_any = True
                # Route through the deferred path: a window present at
                # extension-load time may still be mid-load (see issue #4).
                self._schedule_window_init(win)
        if toplevels and not found_any and not self._windows:
            names = [type(w).__name__ for w in toplevels]
            _log(f"check_new_windows: no NautilusWindow found among {names} — class renamed?")
        return True

    def _on_window_destroyed(self, win: Gtk.Window) -> None:
        state = self._windows.pop(win, None)
        if state:
            tick_id = state.get("stale_release_tick_id")
            overlay = state.get("overlay")
            if (
                tick_id is not None
                and overlay is not None
                and hasattr(overlay, "remove_tick_callback")
            ):
                overlay.remove_tick_callback(tick_id)
            state["stale_release_tick_id"] = None
            state["stale_release_ticks"] = 0
            state.get("stale_generations", []).clear()
            model = state.get("native_hide_model")
            handler = state.get("native_hide_handler")
            if model is not None and handler:
                try:
                    model.disconnect(handler)
                except Exception:
                    pass
            state["native_hide_model"] = None
            state["native_hide_handler"] = None
        # Stop usage poll workers if this was the last window showing our panel.
        self._stop_usage_poll_if_idle()

    def _on_overlay_finalized(self, win: Gtk.Window, state: dict) -> None:
        if state.get("overlay") is None and not state.get("overlay_alive", True):
            return
        was_visible = state.get("visible_view") == VIEW_DISKINFO
        state["overlay"] = None
        state["overlay_alive"] = False
        state["visible_view"] = None
        _log(f"overlay finalized before window destroy for {type(win).__name__}")
        if was_visible:
            GLib.idle_add(self._stop_usage_poll_if_idle)

    def _has_live_overlay(self, state: dict, site: str) -> bool:
        if state.get("overlay") is None or not state.get("overlay_alive", True):
            _log(f"{site}: skip dead overlay")
            return False
        return True

    def _trace_view_set(self, overlay: Gtk.Widget, name: str, site: str) -> None:
        _log(f"{site}: show view '{name}' on {type(overlay).__name__}@0x{id(overlay):x}")

    def _set_visible_view(self, state: dict, name: str, site: str) -> bool:
        if not self._has_live_overlay(state, site):
            return False
        panel = state.get("panel")
        if panel is None:
            return False

        self._trace_view_set(state["overlay"], name, site)
        # files_widget is the always-present Overlay base — never hidden (hiding
        # it would reparent/unmap and risk the GTK_IS_STACK crash). Toggle only
        # the panel overlay's visibility. The panel is FILL/FILL and absorbs all
        # pointer events without needing set_sensitive() on the base. Keyboard
        # type-ahead is blocked separately by the capture-phase key guard
        # (_on_window_key_capture). Do NOT call set_sensitive() on the base
        # (AdwTabView): it covers all tabs, so toggling it disrupts Nautilus
        # keyboard controllers on other tabs and causes freezes in multi-tab.
        panel.set_visible(name == VIEW_DISKINFO)

        state["visible_view"] = name
        # While the panel is shown, hide the vanilla computer:/// contents
        # (icons/labels) underneath via opacity. The vanilla view is itself a
        # .nautilus-grid-view, so its theme background/radius/margin/shadow stay
        # intact and serve as the opaque backdrop behind the panel's transparent
        # heading rows and card gaps. Un-blank when we land on a normal folder.
        self._blank_vanilla_view(state, name == VIEW_DISKINFO)
        return True

    def _blank_vanilla_view(self, state: dict, hidden: bool) -> None:
        """Toggle a CSS class that paints over the vanilla computer:/// file view
        with the panel's background before our overlay panel becomes visible.

        Adding/removing a style class is a same-frame paint change, not a tree
        mutation - it cannot trigger the GTK_IS_STACK reparenting crash class
        (see _set_visible_view). We arm this the moment we know navigation is
        heading to computer:/// (earlier than the title-settle signal that drives
        the overlay toggle), so the vanilla grid is never painted at all on the
        paths we initiate ourselves."""
        files_widget = state.get("files_widget")
        if files_widget is None:
            return
        if hidden:
            files_widget.add_css_class("vanilla-diskinfo-view-hidden")
        else:
            files_widget.remove_css_class("vanilla-diskinfo-view-hidden")

    def _set_computer_sidebar_selected(self, state: dict, selected: bool) -> bool:
        my_computer_listbox = state.get("sidebar_my_computer_listbox")
        sidebar_row = state.get("sidebar_row")
        if my_computer_listbox is None or sidebar_row is None:
            return GLib.SOURCE_REMOVE
        try:
            if sidebar_row.get_parent() is not my_computer_listbox:
                return GLib.SOURCE_REMOVE
            if selected:
                if my_computer_listbox.get_selected_row() is not sidebar_row:
                    my_computer_listbox.select_row(sidebar_row)
            elif my_computer_listbox.get_selected_row() is sidebar_row:
                my_computer_listbox.unselect_all()
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def _on_settings_changed(self, settings: Gio.Settings, key: str) -> None:
        if key == "start-on-disks":
            self._start_on_disks = settings.get_boolean(key)
        elif key in (
            "color-mode",
            "custom-color",
            "custom-gradient-color-1",
            "custom-gradient-color-2",
        ):
            self._apply_bar_color()
        elif key == "show-system-partitions":
            # Needs a rescan because filtered mounts must be re-collected
            self._schedule_live_refresh()
        elif key.startswith("visibility-"):
            # Grouping change only -- no rescan needed, just re-render
            self._repopulate_visible()
        elif key == "preferred-folders":
            self._repopulate_visible()
        elif key.startswith("sidebar-show-"):
            # Sidebar place toggle -- re-apply native row visibility in every window.
            GLib.idle_add(self._reapply_sidebar_visibility)
        elif key == "custom-bookmark-icons":
            # Another window customized a bookmark icon -- re-apply everywhere.
            GLib.idle_add(self._reapply_bookmark_icons_all_windows)
        elif key == "computer-icon":
            # Distro override or dconf edit -- re-pin the Computer row/chip icon.
            GLib.idle_add(self._reapply_computer_icon_all_windows)

    def _get_computer_icon(self) -> str:
        """Symbolic icon name for the Computer row, overridable via the
        computer-icon GSettings key (e.g. a distro .gschema.override)."""
        if self._gsettings is None:
            return COMPUTER_ICON
        icon = self._gsettings.get_string("computer-icon")
        return icon or COMPUTER_ICON

    def _reapply_computer_icon_all_windows(self) -> bool:
        """Re-pin the Computer sidebar row and path bar chip icon in every
        window after a computer-icon settings change."""
        icon_name = self._get_computer_icon()
        for win, state in list(self._windows.items()):
            sidebar_row = state.get("sidebar_row")
            if sidebar_row is not None:
                for w in _all_widgets(sidebar_row):
                    if isinstance(w, Gtk.Image):
                        _pin_icon(w, icon_name)
                try:
                    sidebar_row.set_property("start-icon", Gio.ThemedIcon.new(icon_name))
                except Exception:
                    pass
            self._fix_pathbar_icon(win)
        return GLib.SOURCE_REMOVE

    def _apply_bar_color(self) -> None:
        if not self._gsettings or self._bar_css_display is None:
            return
        mode = self._gsettings.get_string("color-mode")
        if mode == "flat":
            color = self._gsettings.get_string("custom-color")
            css = f".diskinfo-bar block.filled {{ background: {color}; }}".encode()
        elif mode == "gradient":
            c1 = self._gsettings.get_string("custom-gradient-color-1")
            c2 = self._gsettings.get_string("custom-gradient-color-2")
            # Use CSS :dir() so GTK resolves direction per-widget at render time.
            # Gradient spans the filled area directly — no background-size trickery,
            # which is unreliable on older GTK4 (e.g. Ubuntu 22.04 / GTK 4.6.x).
            css = (
                f".diskinfo-bar:dir(ltr) block.filled {{"
                f" background: linear-gradient(to right, {c1} 20%, {c2} 100%); }}"
                f".diskinfo-bar:dir(rtl) block.filled {{"
                f" background: linear-gradient(to left, {c1} 20%, {c2} 100%); }}"
            ).encode()
        else:
            css = b".diskinfo-bar block.filled { background: @accent_bg_color; }"
        self._bar_css_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self._bar_css_display,
            self._bar_css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

    def _read_sort_metadata(self) -> bool:
        """Read sort order from GVfs metadata on computer:///.
        Returns True when the column or direction changed since last read."""
        try:
            f = Gio.File.new_for_uri(DISKS_URI)
            info = f.query_info(
                f"{METADATA_SORT_BY},{METADATA_SORT_REVERSED}",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
            col = info.get_attribute_string(METADATA_SORT_BY) or "name"
            rev_str = info.get_attribute_string(METADATA_SORT_REVERSED) or "false"
            rev = rev_str == "true"
            if col != self._sort_column or rev != self._sort_reverse:
                self._sort_column = col
                self._sort_reverse = rev
                return True
        except Exception:
            pass
        return False

    def _attach_sort_button_watch(self, nautilus_win: Gtk.Window) -> None:
        """Watch the sort GtkMenuButton's active state — arm poll when the sort
        popover opens, disarm (with one final read) when it closes."""
        state = self._windows.get(nautilus_win)
        if not state or state.get("header_motion"):
            return
        btn = self._find_sort_button(nautilus_win)
        if btn is None:
            _log("sort button not found in toolbar")
            return
        btn.connect("notify::active", self._on_sort_button_active, nautilus_win)
        state["header_motion"] = btn  # reuse slot — just marks "already attached"
        _log(f"sort button watch attached ({type(btn).__name__})")

    def _find_sort_button(self, nautilus_win: Gtk.Window):
        """Find the GtkMenuButton inside NautilusViewControls (the sort/view popover button)."""
        # NautilusViewControls has no real buildable_id (auto-generated) and no css class.
        # Tier 2 (class name) is the primary match; tier 4 structural is the fallback.
        view_controls = _find_widget(
            nautilus_win,
            class_name="NautilusViewControls",
            site="_find_sort_button",
        )
        if view_controls:
            for child in _all_widgets(view_controls):
                if isinstance(child, Gtk.MenuButton):
                    return child

        # Structural fallback: navigate via typed Adwaita getters to the content
        # toolbar and find the first MenuButton that isn't the hamburger.
        split_view = next(
            (w for w in _all_widgets(nautilus_win) if isinstance(w, Adw.OverlaySplitView)), None
        )
        if split_view:
            content = split_view.get_content()
            toolbar_view = (
                next((w for w in _all_widgets(content) if isinstance(w, Adw.ToolbarView)), None)
                if content
                else None
            )
            if toolbar_view:
                for w in _all_widgets(toolbar_view):
                    if isinstance(w, Gtk.MenuButton) and w.get_icon_name() != "open-menu-symbolic":
                        _log("_find_sort_button: matched via structural nav (NautilusViewControls)")
                        return w
        return None

    def _on_sort_button_active(self, btn: Gtk.MenuButton, _param, nautilus_win: Gtk.Window) -> None:
        state = self._windows.get(nautilus_win)
        if not state or not self._has_live_overlay(state, "sort button"):
            return
        if state.get("visible_view") != VIEW_DISKINFO:
            return
        if btn.get_active():
            self._sort_hover = True
            if self._sort_poll_id is None:
                _log("sort menu opened → sort poll armed")
                self._sort_poll_id = GLib.timeout_add(_SORT_POLL_MS, self._poll_sort)
        else:
            self._sort_hover = False
            _log("sort menu closed → sort poll disarming")

    def _poll_sort(self) -> bool:
        if self._read_sort_metadata():
            _log(f"sort changed → col='{self._sort_column}' rev={self._sort_reverse}")
            self._repopulate_visible()
            _log(f"sort applied → col='{self._sort_column}' rev={self._sort_reverse}")
        if not self._sort_hover:
            # Menu closed — one final read already done above, now disarm.
            _log("sort poll disarmed")
            self._sort_poll_id = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _read_view_mode(self) -> None:
        """Read current view mode and click policy from Nautilus preferences."""
        try:
            settings = Gio.Settings.new("org.gnome.nautilus.preferences")
            self._view_mode = settings.get_string("default-folder-viewer")
            self._click_policy = settings.get_string("click-policy")
        except Exception:
            pass

    def _watch_view_mode(self) -> None:
        """Subscribe to GSettings so view-mode/click-policy changes are instant."""
        try:
            settings = Gio.Settings.new("org.gnome.nautilus.preferences")
            settings.connect("changed::default-folder-viewer", self._on_view_mode_changed)
            settings.connect("changed::click-policy", self._on_click_policy_changed)
            self._nautilus_prefs = settings  # keep reference
        except Exception:
            pass

    def _on_view_mode_changed(self, settings: Gio.Settings, _key: str) -> None:
        prev = self._view_mode
        self._view_mode = settings.get_string("default-folder-viewer")
        if self._view_mode != prev:
            _log(f"view changed → mode='{self._view_mode}'")
            self._repopulate_visible()

    def _on_click_policy_changed(self, settings: Gio.Settings, _key: str) -> None:
        prev = self._click_policy
        self._click_policy = settings.get_string("click-policy")
        if self._click_policy != prev:
            _log(f"click-policy changed → '{self._click_policy}'")
            self._repopulate_visible()

    # ── Live-refresh helpers ──────────────────────────────────────────────────

    def _on_disk_event(self, _monitor, *_args) -> None:
        """VolumeMonitor signal handler — debounced."""
        self._schedule_live_refresh()

    def _on_proc_mounts_changed(self, _source, _condition) -> bool:
        """/proc/mounts POLLPRI handler — any kernel mount change."""
        self._schedule_live_refresh()
        return GLib.SOURCE_CONTINUE  # keep watching

    def _schedule_live_refresh(self) -> None:
        """Coalesce rapid events (plug → volume-added → mount-added) into one update."""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        GLib.timeout_add(_REFRESH_DEBOUNCE_MS, self._do_live_refresh)

    def _do_live_refresh(self) -> bool:
        self._refresh_pending = False
        _show_sys_parts = (
            self._gsettings.get_boolean("show-system-partitions") if self._gsettings else False
        )
        _refresh(_scan_mounts(_show_sys_parts) + _scan_gio_mounts() + _scan_gio_volumes())
        # Re-discover network places in background; callback will repopulate
        _refresh_network_places(on_done=self._repopulate_visible)
        self._repopulate_visible()
        return GLib.SOURCE_REMOVE

    def _repopulate_visible(self) -> bool:
        """Repopulate whichever windows are showing the disk view."""
        for win, state in list(self._windows.items()):
            if not self._has_live_overlay(state, "repopulate_visible"):
                continue
            if state.get("visible_view") == VIEW_DISKINFO:
                self._populate(win)
        return GLib.SOURCE_REMOVE

    # ── Usage poll workers (armed while panel is visible) ─────────────────────

    def _sweep_local_usage(self) -> None:
        """Worker-thread only: statvfs every local mount, queue changed usage to
        the main thread. Pure-read — never writes _disk_data here (that happens on
        the main thread in _apply_usage_updates via dataclasses.replace)."""
        updates: dict[str, tuple[int, int]] = {}
        for key, m in list(_disk_data.items()):
            if m.is_gio or not m.is_mounted or not m.mountpoint:
                continue
            usage = _root_usage() if m.mountpoint == "/" else _statvfs_usage(m.mountpoint)
            if usage is None:
                continue
            total, free = usage
            if free != m.free or total != m.total:
                updates[key] = (total, free)
        if updates:
            GLib.idle_add(self._apply_usage_updates, updates, priority=GLib.PRIORITY_DEFAULT)

    def _local_usage_worker(self, stop_event: threading.Event) -> None:
        """Background thread: refresh local-mount usage, adapting cadence to write
        activity and gating on disk-busy.

        statvfs blocks for *seconds* and contends with in-flight file operations
        under ext4 journal load (confirmed: polling statvfs during a copy/delete
        made those operations sluggish while the panel was visible). So normally we
        check /proc/diskstats first (cheap, no contention): if the disk has I/O in
        flight we skip the sweep — no statvfs, no contention.

        Two refinements make the panel feel live without breaking that gate:
          • An immediate ungated sweep on entry, so arriving at the panel (e.g.
            navigating back after a copy) shows fresh numbers at once instead of
            the stale cache _populate() rendered.
          • A /proc/meminfo Dirty+Writeback forward signal (cadence only, never
            used to estimate free space): poll fast while writes are buffered, and
            force one definitive sweep the instant dirty pages drain — the flush,
            i.e. exactly when statvfs finally changes — even if the busy-gate would
            otherwise skip it.

        Self-disarms when the panel is hidden (stop_event)."""
        prev_ticks, _ = _read_io_busy()
        prev_t = time.monotonic()
        was_active = _read_dirty_bytes() >= _DIRTY_ACTIVE_THRESHOLD
        while True:
            interval = _USAGE_POLL_FAST_MS if was_active else _USAGE_GATE_MS
            if stop_event.wait(interval / 1000.0):
                break

            now = time.monotonic()
            ticks, inflight = _read_io_busy()
            busy_ms = ticks - prev_ticks
            elapsed_ms = (now - prev_t) * 1000
            prev_ticks, prev_t = ticks, now

            is_active = _read_dirty_bytes() >= _DIRTY_ACTIVE_THRESHOLD
            just_flushed = was_active and not is_active  # buffered writes hit disk
            was_active = is_active

            # Skip while the disk is busy — except right after a flush, when the
            # post-flush value is exactly what we need and must not be missed.
            if not just_flushed and (inflight > 0 or busy_ms > _USAGE_BUSY_RATIO * elapsed_ms):
                continue

            self._sweep_local_usage()

    def _net_usage_tick(self) -> bool:
        """GLib timer callback: fire async D-Bus usage queries for all GVfs/network mounts."""
        attrs = f"{Gio.FILE_ATTRIBUTE_FILESYSTEM_SIZE},{Gio.FILE_ATTRIBUTE_FILESYSTEM_FREE}"
        for key, m in list(_disk_data.items()):
            if not m.is_gio:
                continue
            Gio.File.new_for_uri(m.nav_uri).query_filesystem_info_async(
                attrs,
                GLib.PRIORITY_DEFAULT,
                self._net_poll_cancellable,
                self._on_net_info_ready,
                key,
            )
        return GLib.SOURCE_CONTINUE

    def _on_net_info_ready(self, gfile: Gio.File, result: Gio.AsyncResult, key: str) -> None:
        """Async callback (main thread): apply network mount usage update."""
        try:
            info = gfile.query_filesystem_info_finish(result)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                _log(f"net usage query failed: {e.message}")
            return
        total = info.get_attribute_uint64(Gio.FILE_ATTRIBUTE_FILESYSTEM_SIZE)
        free = info.get_attribute_uint64(Gio.FILE_ATTRIBUTE_FILESYSTEM_FREE)
        if total <= 0 or key not in _disk_data:
            return
        m = _disk_data[key]
        if total != m.total or free != m.free:
            self._apply_usage_updates({key: (total, free)})

    def _apply_usage_updates(self, updates: dict) -> bool:
        """Main-thread callback: patch _disk_data and update card widgets in place."""
        global _disk_data
        for key, (total, free) in updates.items():
            if key not in _disk_data:
                continue
            _disk_data[key] = dataclasses.replace(_disk_data[key], total=total, free=free)
            for state in self._windows.values():
                if not self._has_live_overlay(state, "apply_usage_updates"):
                    continue
                if state.get("visible_view") != VIEW_DISKINFO:
                    continue
                self._update_card_usage(state, key, total, free)
        return GLib.SOURCE_REMOVE

    def _update_card_usage(self, state: dict, key: str, total: int, free: int) -> None:
        """Patch a disk card's LevelBar/sub-label in place via the O(1) card_widgets registry."""
        card = state.get("card_widgets", {}).get(key)
        if card is not None:
            card.update_usage(_disk_data[key])

    def _ensure_usage_poll_running(self) -> None:
        """Arm both usage poll workers if not already running."""
        if self._local_poll_stop is None:
            ev = threading.Event()
            self._local_poll_stop = ev
            threading.Thread(target=self._local_usage_worker, args=(ev,), daemon=True).start()
        if self._net_poll_timer_id is None:
            self._net_poll_cancellable = Gio.Cancellable()
            self._net_usage_tick()
            self._net_poll_timer_id = GLib.timeout_add(_USAGE_POLL_NETWORK_MS, self._net_usage_tick)

    def _stop_usage_poll_if_idle(self) -> None:
        """Disarm poll workers when no window is showing the disk panel."""
        any_visible = any(
            st.get("overlay") is not None
            and st.get("overlay_alive", True)
            and st.get("visible_view") == VIEW_DISKINFO
            for st in self._windows.values()
        )
        if not any_visible:
            if self._local_poll_stop is not None:
                self._local_poll_stop.set()
                self._local_poll_stop = None
            if self._net_poll_timer_id is not None:
                GLib.source_remove(self._net_poll_timer_id)
                self._net_poll_timer_id = None
            if self._net_poll_cancellable is not None:
                self._net_poll_cancellable.cancel()
                self._net_poll_cancellable = None

    def _inject_overlay(self, nautilus_win: Gtk.Window) -> bool:
        split_view = None
        for w in _all_widgets(nautilus_win):
            if isinstance(w, Adw.OverlaySplitView):
                split_view = w
                break
        if not split_view:
            _log("inject_overlay: Adw.OverlaySplitView not found — widget tree may have changed")
            return False

        toolbar_view = None
        right = split_view.get_content()
        if right:
            for w in _all_widgets(right):
                if isinstance(w, Adw.ToolbarView):
                    toolbar_view = w
                    break

        panel, grid_host, grid_box = self._build_panel(nautilus_win)
        # Use Gtk.Overlay: files view is the always-present base; our panel floats
        # on top as an overlay child, visible only on computer:///. The Overlay type
        # is opaque to Nautilus's gtk_widget_get_ancestor(view, GTK_TYPE_STACK) walk,
        # so it never confuses Nautilus's internal view GtkStack (unlike a Gtk.Stack
        # wrapper, which caused the GTK_IS_STACK assertion / SIGSEGV on GNOME 43+).
        overlay = Gtk.Overlay()

        # Panel must fill the full Overlay area so the files view doesn't show through.
        panel.set_halign(Gtk.Align.FILL)
        panel.set_valign(Gtk.Align.FILL)

        if not DEBUG_MAIN_VIEW_ACTIVE:
            # Main-view site disabled: keep the overlay/panel ORPHAN (never inserted
            # into Nautilus's tree) so other sites can still be exercised/isolated.
            files_widget = None
            _log("inject_overlay: DEBUG_MAIN_VIEW_ACTIVE=False — overlay kept orphan")
        elif toolbar_view:
            files_widget = toolbar_view.get_content()
            if not files_widget:
                return False
            toolbar_view.set_content(overlay)
            overlay.set_child(files_widget)
            overlay.add_overlay(panel)
        else:
            files_widget = right
            if not files_widget:
                return False
            split_view.set_content(overlay)
            overlay.set_child(files_widget)
            overlay.add_overlay(panel)

        # Start with the files view — panel hidden until title changes to computer:///.
        self._trace_view_set(overlay, VIEW_FILES, "inject_overlay initial")
        panel.set_visible(False)

        self._windows[nautilus_win] = {
            "overlay": overlay,
            "overlay_alive": True,
            "visible_view": VIEW_FILES,
            "files_widget": files_widget,
            "panel": panel,
            "grid_host": grid_host,
            "grid_box": grid_box,
            "section_flows": [],
            "card_widgets": {},  # key → MyComputerDiskCard
            "stale_generations": [],
            "stale_release_tick_id": None,
            "stale_release_ticks": 0,
            "_deselecting": False,
            "force_disks": False,
            "initial_title": None,
            "start_on_computer": self._start_on_disks,
            "awaiting_disks": False,
            "selected_mount_key": None,
            "selected_folder_key": None,
            "header_motion": None,  # Gtk.EventControllerMotion on the header bar
            "native_hide_model": None,  # observe_children() model of native listbox
            "native_hide_handler": None,  # items-changed handler id on that model
            "native_hide_pending": False,  # coalesces re-hide bursts into one idle pass
        }
        overlay.weak_ref(
            lambda w=nautilus_win, st=self._windows.get(nautilus_win): (
                self._on_overlay_finalized(w, st) if st is not None else None
            )
        )

        # Capture-phase key guard on the window: Nautilus's "type to search"
        # type-ahead is hooked above keyboard focus, so neither hiding nor
        # de-focusing the covered file view stops it. A controller at the top of
        # the capture chain sees keystrokes first and swallows plain printable
        # ones while the panel is shown — so typing doesn't reopen the vanilla
        # computer:/// search. Modified shortcuts (Ctrl/Alt/Super) and control
        # keys (arrows, Tab, Enter, Esc) always pass through.
        key_guard = Gtk.EventControllerKey()
        key_guard.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_guard.connect("key-pressed", self._on_window_key_capture, nautilus_win)
        nautilus_win.add_controller(key_guard)

        # If this window is headed to computer:///, let the later title-change
        # path do the first populate + switch once Nautilus has settled.

        return True

    def _on_window_key_capture(self, _ctrl, keyval, _keycode, gtk_state, win) -> bool:
        """Swallow plain printable keystrokes while our panel is shown, so
        Nautilus's window-level type-ahead search doesn't reopen the file view."""
        state = self._windows.get(win)
        if not state or state.get("visible_view") != VIEW_DISKINFO:
            return False
        # Let modified shortcuts through (Ctrl+L, Alt+Left, Super, …).
        if gtk_state & (
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK
        ):
            return False
        # Only swallow printable characters (>= space). Control keys — arrows,
        # Tab, Enter, Esc, function keys — map to unicode < 0x20 and pass through.
        if Gdk.keyval_to_unicode(keyval) < 0x20:
            return False
        # If the user opened a text entry (Ctrl+L location bar, Ctrl+F search),
        # the focused widget is an Editable — let it receive the keystroke.
        focused = win.get_focus()
        if focused is not None and isinstance(focused, Gtk.Editable):
            return False
        return True

    # ── Panel construction ────────────────────────────────────────────────────

    def _new_grid_box(self) -> Gtk.Box:
        grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        grid_box.set_hexpand(True)
        grid_box.set_valign(Gtk.Align.START)
        grid_box.set_margin_start(18)
        grid_box.set_margin_end(18)
        grid_box.set_margin_top(18)
        grid_box.set_margin_bottom(18)
        return grid_box

    def _release_stale_generations(self, state: dict) -> bool:
        state.get("stale_generations", []).clear()
        state["stale_release_tick_id"] = None
        state["stale_release_ticks"] = 0
        return GLib.SOURCE_REMOVE

    def _queue_stale_generation_release(self, state: dict, root: Gtk.Widget) -> None:
        stale = state.setdefault("stale_generations", [])
        stale.append(root)
        state["stale_release_ticks"] = _STALE_RELEASE_FRAMES
        if state.get("stale_release_tick_id") is not None:
            return

        owner = state.get("overlay")
        if owner is None or not hasattr(owner, "add_tick_callback"):
            GLib.timeout_add(50, lambda st=state: self._release_stale_generations(st))
            return

        def _release_on_tick(_widget, _frame_clock, st=state):
            ticks_left = max(0, st.get("stale_release_ticks", 0) - 1)
            st["stale_release_ticks"] = ticks_left
            if ticks_left > 0:
                return GLib.SOURCE_CONTINUE
            return self._release_stale_generations(st)

        state["stale_release_tick_id"] = owner.add_tick_callback(_release_on_tick)

    def _build_panel(self, win: Gtk.Window) -> tuple:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.set_hexpand(True)
        panel.set_vexpand(True)
        panel.get_style_context().add_class("diskinfo-panel")
        panel.add_css_class("nautilus-grid-view")

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        grid_box = self._new_grid_box()

        scroll.set_child(grid_box)
        panel.append(scroll)

        bg_deselect = Gtk.GestureClick()
        bg_deselect.set_button(0)
        bg_deselect.connect("pressed", self._on_panel_clicked, win)
        scroll.add_controller(bg_deselect)

        return panel, scroll, grid_box

    def _populate(self, win: Gtk.Window) -> None:
        state = self._windows.get(win)
        if state is None:
            return

        grid_box = self._new_grid_box()
        section_flows: list[Gtk.FlowBox] = []
        card_widgets = {}
        folder_card_widgets = {}

        col = self._sort_column
        rev = self._sort_reverse

        def _sort_key(m: MountInfo):
            if col == "size":
                return m.total
            return (m.display_name or "").lower()

        # Build PanelGroup objects, reading visibility state from gsettings
        groups: dict[str, PanelGroup] = {}
        for gkey, glabel, gskey in _GROUP_SPEC:
            if gskey is None:
                # "On this Computer" is the merge target -- always visible, never merged
                groups[gkey] = PanelGroup(key=gkey, label=_(glabel), visible=True, merged=False)
                continue
            vis_str = self._gsettings.get_string(gskey) if self._gsettings else "visible"
            visible = vis_str != "hidden"
            merged = vis_str == "merged"
            groups[gkey] = PanelGroup(key=gkey, label=_(glabel), visible=visible, merged=merged)

        # Classify each mount into its group
        for m in _disk_data.values():
            groups[_classify_mount(m)].add_item(m)

        active_uris = {m.nav_uri for m in _disk_data.values()}
        for place in _network_places:
            if place.nav_uri not in active_uris:
                groups["network"].add_item(place)

        # Sort each group's items
        for gkey, group in groups.items():
            if gkey in ("system", "local"):
                if col == "type":
                    group.sort_items(key_func=_get_local_mount_tier, reverse=False)
                else:
                    mounted = [m for m in group.items if m.is_mounted]
                    unmounted = [m for m in group.items if not m.is_mounted]
                    mounted.sort(key=_sort_key, reverse=rev)
                    unmounted.sort(key=_sort_key, reverse=rev)
                    group.items = mounted + unmounted
            elif gkey == "removable":
                mounted = [m for m in group.items if m.is_mounted]
                unmounted = [m for m in group.items if not m.is_mounted]
                mounted.sort(key=_sort_key, reverse=rev)
                unmounted.sort(key=_sort_key, reverse=rev)
                group.items = mounted + unmounted
            else:
                group.sort_items(key_func=_sort_key, reverse=rev)

        # Merge pass: fold items from merged groups into "local", preserving origin key
        # Each entry in local_extra is (MountInfo, origin_group_key)
        local_extra: list[tuple] = []
        # Fixed group-level order for sort-by-type within the merged "On this Computer" group:
        # system=0, local=1, removable=2, disc=3, network=4
        _merge_type_order = {"system": 0, "local": 1, "removable": 2, "disc": 3, "network": 4}
        for gkey, _gl, _gs in _GROUP_SPEC:
            group = groups[gkey]
            if gkey != "local" and group.merged:
                for m in group.items:
                    local_extra.append((m, gkey))

        # Preferred Folders group (issue #30): rendered above the disk groups
        show_folders = (
            self._gsettings.get_string("visibility-preferred-folders") != "hidden"
            if self._gsettings
            else True
        )
        if show_folders:
            folders = preferred_folders.load_preferred_folders(self._gsettings)
            _folder_data.clear()
            _folder_data.update({pf.key: pf for pf in folders})
            for pf in folders:
                if pf.key not in preferred_folders.PREFERRED_TOKENS:
                    self._refresh_folder_metadata_async(pf)
            self._sync_folder_rename_watchers(folders)
            if folders:
                section = MyComputerCardSection(
                    self,
                    win,
                    _("Preferred Folders"),
                    self._view_mode,
                    max_cols=_FOLDER_FLOW_COLS_GRID,
                    col_spacing=_FOLDER_CARD_SPACING,
                    row_spacing=_FOLDER_CARD_ROW_SPACING,
                    always_grid=True,
                    justify=True,
                    card_width=_folder_card_width(),
                )
                section_flows.append(section.flow)

                for pf in folders:
                    card = MyComputerFolderCard(self, win, self._view_mode, pf)
                    section.add_card(card)
                    folder_card_widgets[pf.key] = card

                grid_box.append(section)
        else:
            _folder_data.clear()
            self._sync_folder_rename_watchers([])

        for gkey, _glabel, _gskey in _GROUP_SPEC:
            group = groups[gkey]
            # "local" is the merge target: render it whenever it has its own items
            # OR has received merged items, even if the group itself is set to hidden.
            if gkey == "local":
                if not group.visible and not local_extra:
                    continue
            elif not group.visible or group.merged:
                continue

            # For "local", append any merged items (with their origin keys).
            # If local itself is hidden, only the merged extras show.
            render_items: list[tuple]  # (MountInfo, icon_group_key)
            if gkey == "local":
                own = [(m, "local") for m in group.items] if group.visible else []
                render_items = own + local_extra
                if col == "type" and local_extra:
                    # Sort the combined list by group-level tier, then intra-group tier
                    def _merged_type_key(entry, _order=_merge_type_order):
                        m, origin = entry
                        group_tier = _order.get(origin, 5)
                        if origin in ("system", "local"):
                            sub = _get_local_mount_tier(m)
                        else:
                            sub = (0 if m.is_mounted else 1, (m.display_name or "").lower())
                        return (group_tier,) + sub

                    render_items.sort(key=_merged_type_key)
            else:
                render_items = [(m, gkey) for m in group.items]

            if not render_items:
                continue

            section = MyComputerCardSection(
                self,
                win,
                group.label,
                self._view_mode,
                max_cols=_FLOW_COLS_GRID,
                col_spacing=_DISK_CARD_SPACING,
                row_spacing=_DISK_CARD_ROW_SPACING,
                homogeneous=True,
                max_card_width=_CARD_WIDTH,
            )
            section_flows.append(section.flow)

            for m, origin_key in render_items:
                card = MyComputerDiskCard(self, win, self._view_mode, m, origin_key)
                section.add_card(card)
                card_widgets[m.key] = card

            grid_box.append(section)

        old_grid_box = state.get("grid_box")
        state["grid_box"] = grid_box
        state["section_flows"] = section_flows
        state["card_widgets"] = card_widgets
        state["folder_card_widgets"] = folder_card_widgets
        state["grid_host"].set_child(grid_box)
        if old_grid_box is not None:
            self._queue_stale_generation_release(state, old_grid_box)

        # Restore the previously selected card, or explicitly clear all selections.
        # Needed on every populate (first show AND live refresh): FlowBox with SINGLE
        # selection mode can auto-select a child when the widget gains keyboard focus,
        # so we must be explicit here rather than relying on the widget's default state.
        state["_deselecting"] = True
        for flow in section_flows:
            flow.unselect_all()
        state["_deselecting"] = False
        sel_mount = state.get("selected_mount_key")
        sel_folder = state.get("selected_folder_key")
        if sel_mount and sel_mount in card_widgets:
            wrapper = card_widgets[sel_mount].get_parent()
            if isinstance(wrapper, Gtk.FlowBoxChild):
                wrapper.get_parent().select_child(wrapper)
        elif sel_folder and sel_folder in folder_card_widgets:
            wrapper = folder_card_widgets[sel_folder].get_parent()
            if isinstance(wrapper, Gtk.FlowBoxChild):
                wrapper.get_parent().select_child(wrapper)
        else:
            state["selected_mount_key"] = None
            state["selected_folder_key"] = None

        self._apply_bar_color()

    def _refresh_folder_metadata_async(self, pf: "PreferredFolder") -> None:
        """Resolve real display-name/icon for a raw-URI preferred folder without blocking,
        then patch any rendered cards in place via the folder_card_widgets registry."""
        gfile = Gio.File.new_for_uri(pf.nav_uri)
        gfile.query_info_async(
            "standard::display-name,standard::icon,standard::is-hidden",
            Gio.FileQueryInfoFlags.NONE,
            GLib.PRIORITY_DEFAULT,
            self._folder_refresh_cancellable,
            self._on_folder_metadata_ready,
            pf.key,
        )

    def _on_folder_metadata_ready(
        self, gfile: Gio.File, result: Gio.AsyncResult, folder_key: str
    ) -> None:
        try:
            info = gfile.query_info_finish(result)
        except GLib.Error:
            return
        pf = _folder_data.get(folder_key)
        if pf is None:
            return
        display_name = info.get_display_name() or pf.display_name
        gio_icon = info.get_icon()
        is_hidden = info.get_attribute_boolean("standard::is-hidden")
        new_pf = dataclasses.replace(
            pf, display_name=display_name, gio_icon=gio_icon, is_hidden=is_hidden
        )
        _folder_data[folder_key] = new_pf
        for state in self._windows.values():
            card = state.get("folder_card_widgets", {}).get(folder_key)
            if card is not None:
                card.update_metadata(new_pf)

    def _sync_folder_rename_watchers(self, folders: list) -> None:
        """Arm a Gio.FileMonitor (WATCH_MOVES) on the parent directory of each raw-URI
        preferred folder so a rename/move is caught live and the stored GSettings URI
        is corrected -- without this, a renamed folder keeps showing its old name
        forever (the stored URI no longer resolves, so the async metadata refresh just
        fails silently).

        Monitoring the folder itself only sees the "self" side of a move (a bare
        DELETED, no paired new path) -- the parent directory is the only vantage point
        that sees both the move-out and move-in and can pair them into RENAMED.

        Token-based folders (Home, Documents, ...) aren't watched: their URI is
        resolved fresh from GLib.get_user_special_dir() every load, so they can't go
        stale the same way.
        """
        live_keys = {pf.key for pf in folders if pf.key not in preferred_folders.PREFERRED_TOKENS}
        self._watched_folder_keys = live_keys
        live_parents = set()
        for key in live_keys:
            parent = Gio.File.new_for_uri(key).get_parent()
            if parent is not None:
                live_parents.add(parent.get_uri())

        for parent_uri in list(self._folder_monitors):
            if parent_uri not in live_parents:
                self._folder_monitors.pop(parent_uri).cancel()
        for parent_uri in live_parents:
            if parent_uri in self._folder_monitors:
                continue
            try:
                monitor = Gio.File.new_for_uri(parent_uri).monitor(
                    Gio.FileMonitorFlags.WATCH_MOVES, None
                )
                monitor.connect("changed", self._on_preferred_folder_file_changed)
                self._folder_monitors[parent_uri] = monitor
            except GLib.Error as e:
                _log(f"_sync_folder_rename_watchers: monitor failed for {parent_uri}: {e.message}")

    def _on_preferred_folder_file_changed(
        self,
        _monitor: Gio.FileMonitor,
        file: Gio.File,
        other_file: Gio.File | None,
        event_type: Gio.FileMonitorEvent,
    ) -> None:
        if event_type != Gio.FileMonitorEvent.RENAMED or other_file is None:
            return
        old_uri = file.get_uri()
        if old_uri not in self._watched_folder_keys or not self._gsettings:
            return
        new_uri = other_file.get_uri()
        entries = self._get_preferred_folders()
        if old_uri not in entries:
            return
        entries[entries.index(old_uri)] = new_uri
        self._gsettings.set_value("preferred-folders", GLib.Variant("as", entries))

    def _on_card_activated(self, _flow_box, child: Gtk.FlowBoxChild, win: Gtk.Window) -> None:
        card = child.get_child()
        if card is None:
            return
        if isinstance(card, MyComputerDiskCard) and not card.model.is_mounted:
            self._do_mount(card.model, win)
            return
        GLib.idle_add(self._navigate_to, card.nav_uri, win)

    def _on_flow_selection_changed(self, flow_box: Gtk.FlowBox, win: Gtk.Window) -> None:
        state = self._windows.get(win)
        if not state or state.get("_deselecting"):
            return
        selected = flow_box.get_selected_children()
        if selected:
            card = selected[0].get_child()
            is_disk = isinstance(card, MyComputerDiskCard)
            is_folder = isinstance(card, MyComputerFolderCard)
            state["selected_mount_key"] = card.model.key if is_disk else None
            state["selected_folder_key"] = card.model.key if is_folder else None
        else:
            state["selected_mount_key"] = None
            state["selected_folder_key"] = None
            return
        state["_deselecting"] = True
        for other_flow in state.get("section_flows", []):
            if other_flow is not flow_box:
                other_flow.unselect_all()
        state["_deselecting"] = False

    # ── Location change handler ───────────────────────────────────────────────

    def _on_title_changed(self, win: Gtk.Window, _param) -> None:
        state = self._windows.get(win)
        if not state:
            return
        if not self._has_live_overlay(state, "title changed"):
            return

        current_title = win.get_title() or ""
        in_view = _window_is_at_disks(win)

        # A transient/empty title ("Loading…") means the window hasn't resolved
        # its location yet. Never act on it: it must not consume the one-shot
        # start-on-computer flag, nor flip the overlay to the file view.
        if _is_unsettled_title(current_title):
            return

        # While the startup navigation to computer:/// is still in flight, keep
        # the panel pinned. Intermediate titles (e.g. a lingering "Home") must
        # not flip the overlay to the file view and cause a flash.
        if state.get("awaiting_disks"):
            if in_view:
                state["awaiting_disks"] = False  # arrived, fall through
            else:
                return

        if state.get("start_on_computer"):
            state["start_on_computer"] = False
            if current_title == _HOME_TITLE:
                self._navigate_to_disks(win)
                return

        if state["force_disks"]:
            if state["initial_title"] is None:
                state["initial_title"] = current_title
            elif current_title != state["initial_title"] and not in_view:
                state["force_disks"] = False
            else:
                in_view = True

        current = state.get("visible_view")
        if in_view:
            if current != VIEW_DISKINFO:
                self._populate(win)
                if not self._set_visible_view(state, VIEW_DISKINFO, "title changed show diskinfo"):
                    return
                self._ensure_usage_poll_running()

            GLib.idle_add(self._set_computer_sidebar_selected, state, True)

            # Re-pin the chrome icons (path-bar chip + sidebar row) every time we
            # arrive at the computer view. This must run even when the overlay is
            # already showing the panel — on the start-on-disks path the panel is
            # pre-shown before navigation completes, so the chip only gains its
            # "Computer" label here, after the overlay is already DISKINFO.
            if DEBUG_PATHBAR_ACTIVE:
                GLib.idle_add(lambda w=win: self._fix_pathbar_icon(w) or False)
            if DEBUG_SORT_WATCH_ACTIVE:
                GLib.idle_add(lambda w=win: self._attach_sort_button_watch(w) or False)
        elif not in_view and current != VIEW_FILES:
            if state:
                state["_deselecting"] = True
                for flow in state.get("section_flows", []):
                    flow.unselect_all()
                state["_deselecting"] = False
                state["selected_mount_key"] = None
                state["selected_folder_key"] = None
            # Re-derive our sidebar highlight from the aggregate native selection
            # rather than blindly unselecting. The Computer row is selected
            # manually on entry (no live native row to mirror), so on exit nothing
            # fires a native signal to clear it. Re-running the mirror sync clears
            # our row when the destination (e.g. /tmp/) is not one of our places,
            # and leaves it intact when the mirror already picked an owned place.
            sync = state.get("sidebar_sync")
            if callable(sync):
                GLib.idle_add(lambda s=sync: (s(), False)[1])
            else:
                GLib.idle_add(self._set_computer_sidebar_selected, state, False)
            if not self._set_visible_view(state, VIEW_FILES, "title changed show files"):
                return
            self._stop_usage_poll_if_idle()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _attach_flow_shortcuts(self, flow_box: Gtk.FlowBox, win: Gtk.Window) -> None:
        """Declarative Ctrl/Shift/Alt+Return shortcuts for the focused card,
        mirroring how native Nautilus wires a Gtk.ShortcutController onto its
        grid cells, rather than hand-parsing modifier bits off a raw key event.

        Must live on the FlowBox itself, not the card: FlowBoxChild (not our
        card widget) is the actual keyboard focus target, and GTK's shortcut
        search walks up from the focused widget through its ancestors -- the
        FlowBox is one, the card is not. Plain Return is left alone here; it
        already works natively via FlowBox's own "child-activated" binding
        (see _on_card_activated), so duplicating it would be redundant.
        """
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.LOCAL)
        for accel, kind in (
            ("<Control>Return", "tab"),
            ("<Shift>Return", "window"),
            ("<Alt>Return", "properties"),
        ):
            trigger = Gtk.ShortcutTrigger.parse_string(accel)
            action = Gtk.CallbackAction.new(
                lambda w, _args, win=win, kind=kind: self._activate_focused_card(w, win, kind)
            )
            controller.add_shortcut(Gtk.Shortcut.new(trigger, action))
        flow_box.add_controller(controller)

    def _activate_focused_card(self, flow_box: Gtk.FlowBox, win: Gtk.Window, kind: str) -> bool:
        focus_child = flow_box.get_focus_child()
        if focus_child is None:
            return False
        row = focus_child.get_child()
        if row is None:
            return False

        nav_uri = row.nav_uri
        if isinstance(row, MyComputerDiskCard) and not row.model.is_mounted:
            return False

        if kind == "tab":
            self._do_open_tab(nav_uri, win)
        elif kind == "window":
            self._do_open_window(nav_uri)
        elif kind == "properties":
            if not nav_uri:
                return False
            self._do_properties(nav_uri, win)
        return True

    def _do_open_with(self, nav_uri: str, win: Gtk.Window) -> None:
        """Show an app chooser for nav_uri as an in-window sheet (Adw.Dialog),
        matching how native Nautilus presents its own "Open With…" - a custom
        AdwDialog compiled into the nautilus binary, with no public API we
        could call directly. Built directly from Gio.AppInfo rather than
        Gtk.AppChooserDialog: that stock widget is a Gtk.Dialog (always a
        separate top-level window, never an attached sheet), and its
        "View All Apps…" / "Find New Apps…" extras plus collapsed search
        toggle are private template internals with no supported way to
        customize or remove. Used by the "Open With…" card menu item; only
        ever called for local file:// URIs, always folders in this extension,
        so content type is hardcoded to inode/directory."""
        if not nav_uri.startswith("file://"):
            return

        content_type = "inode/directory"
        recommended = list(Gio.AppInfo.get_recommended_for_type(content_type))
        recommended_ids = {info.get_id() for info in recommended}
        other = [
            info
            for info in Gio.AppInfo.get_all()
            if info.get_id() not in recommended_ids and info.should_show()
        ]
        recommended.sort(key=lambda i: i.get_display_name().lower())
        other.sort(key=lambda i: i.get_display_name().lower())

        file_name = Gio.File.new_for_uri(nav_uri).get_basename() or nav_uri

        dialog = Adw.Dialog()
        dialog.set_title(_("Open Folder"))
        dialog.set_content_width(420)
        dialog.set_content_height(560)

        toolbar_view = Adw.ToolbarView()
        dialog.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda *_a: dialog.close())
        header.pack_start(cancel_button)
        open_button = Gtk.Button(label=_("Open"))
        open_button.add_css_class("suggested-action")
        open_button.set_sensitive(False)
        header.pack_end(open_button)
        toolbar_view.add_top_bar(header)

        # Search entry as its own toolbar row (native Nautilus: an Adw.Bin with
        # the "toolbar" style class), not inside the margined content box - this
        # is what makes it span edge-to-edge, aligned with the header buttons.
        search_bin = Adw.Bin()
        search_bin.add_css_class("toolbar")
        search_entry = Gtk.SearchEntry()
        search_entry.set_hexpand(True)
        search_bin.set_child(search_entry)
        toolbar_view.add_top_bar(search_bin)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        toolbar_view.set_content(content)

        description = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        description.set_markup(
            _("Choose an app to open <b>%s</b>") % GLib.markup_escape_text(file_name)
        )
        content.append(description)

        # has-frame gives the native rounded-corner/bordered look (matches
        # NautilusAppChooserWidget's ScrolledWindow); the listbox itself stays
        # unstyled so rows render without the .boxed-list per-row separators.
        scroller = Gtk.ScrolledWindow()
        scroller.set_has_frame(True)
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content.append(scroller)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.set_activate_on_single_click(False)
        scroller.set_child(listbox)

        def _make_header_row(text: str, *, first: bool) -> Gtk.ListBoxRow:
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_activatable(False)
            label = Gtk.Label(label=text, xalign=0.0)
            label.add_css_class("heading")
            label.set_margin_start(6)
            label.set_margin_top(6 if first else 16)
            label.set_margin_bottom(6)
            row.set_child(label)
            return row

        def _make_app_row(info: Gio.AppInfo) -> Gtk.ListBoxRow:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(6)
            box.set_margin_end(6)
            icon = info.get_icon()
            image = (
                Gtk.Image.new_from_gicon(icon)
                if icon
                else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
            )
            image.set_pixel_size(32)
            box.append(image)
            label = Gtk.Label(label=info.get_display_name(), xalign=0.0)
            box.append(label)
            row.set_child(box)
            row._app_info = info
            return row

        def _populate(filter_text: str = "") -> None:
            child = listbox.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                listbox.remove(child)
                child = nxt
            filt = filter_text.strip().lower()
            first_app_row = None
            is_first_group = True
            for title, apps in ((_("Recommended Apps"), recommended), (_("Other Apps"), other)):
                matches = [a for a in apps if not filt or filt in a.get_display_name().lower()]
                if not matches:
                    continue
                listbox.append(_make_header_row(title, first=is_first_group))
                is_first_group = False
                for info in matches:
                    app_row = _make_app_row(info)
                    listbox.append(app_row)
                    if first_app_row is None:
                        first_app_row = app_row
            if first_app_row is not None:
                listbox.select_row(first_app_row)
            else:
                empty = Gtk.ListBoxRow()
                empty.set_selectable(False)
                empty.set_activatable(False)
                label = Gtk.Label(label=_("No applications found."))
                label.add_css_class("dim-label")
                label.set_margin_top(24)
                label.set_margin_bottom(24)
                empty.set_child(label)
                listbox.append(empty)

        def _selected_app_info():
            row = listbox.get_selected_row()
            return getattr(row, "_app_info", None) if row else None

        def _launch_and_close() -> None:
            info = _selected_app_info()
            dialog.close()
            if info:
                try:
                    info.launch_uris([nav_uri], None)
                except GLib.Error as e:
                    _log(f"Open With launch failed: {e}")

        search_entry.connect("search-changed", lambda e: _populate(e.get_text()))
        search_entry.connect("activate", lambda *_a: _launch_and_close())
        listbox.connect(
            "row-selected",
            lambda _lb, row: open_button.set_sensitive(getattr(row, "_app_info", None) is not None),
        )
        listbox.connect(
            "row-activated",
            lambda _lb, row: _launch_and_close() if getattr(row, "_app_info", None) else None,
        )
        open_button.connect("clicked", lambda *_a: _launch_and_close())

        _populate()
        dialog.present(win)
        search_entry.grab_focus()

    def _on_panel_clicked(self, _gesture, _n, _x, _y, win: Gtk.Window) -> None:
        state = self._windows.get(win)
        if not state:
            return
        state["_deselecting"] = True
        for flow in state.get("section_flows", []):
            flow.unselect_all()
        state["_deselecting"] = False
        state["selected_mount_key"] = None
        state["selected_folder_key"] = None

    def _on_card_right_clicked(self, gesture, _n, x, y, win: Gtk.Window, row: Gtk.Box) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        if isinstance(row, MyComputerDiskCard):
            m = _disk_data.get(row.model.key)
            if not m or not callable(m.menu):
                return
            ctx_menu = m.menu(self, win, m)
        elif isinstance(row, MyComputerFolderCard):
            pf = _folder_data.get(row.model.key)
            if not pf or not callable(pf.menu):
                return
            ctx_menu = pf.menu(self, win, pf)
        else:
            return

        popover = ctx_menu.build_popover(row, "diskrow")
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _do_open(self, nav_uri: str, win: Gtk.Window) -> None:
        GLib.idle_add(self._navigate_to, nav_uri, win)

    def _do_open_tab(self, nav_uri: str, win: Gtk.Window) -> None:
        uri = nav_uri

        tab_view = next(
            (w for w in _all_widgets(win) if isinstance(w, Adw.TabView)),
            None,
        )
        pages_before = tab_view.get_n_pages() if tab_view else 0

        # Switch to the files view first — new-tab action requires the TabView to be visible.
        state = self._windows.get(win)
        if state and self._set_visible_view(state, VIEW_FILES, "open_tab show files"):
            self._stop_usage_poll_if_idle()

        attempt = [0]

        def _fire_and_wait():
            Gio.ActionGroup.activate_action(win, "new-tab", None)

            def _wait_for_tab():
                n = tab_view.get_n_pages() if tab_view else 0
                if not (tab_view and n > pages_before):
                    attempt[0] += 1
                    if attempt[0] >= 20:
                        return GLib.SOURCE_REMOVE
                    return GLib.SOURCE_CONTINUE

                # Navigate by index, not selected page — avoids racing with
                # concurrent rapid tab-opens that share the same pages_before.
                page = tab_view.get_nth_page(pages_before)
                if page:
                    slot = page.get_child()
                    if slot and slot.activate_action("slot.open-location", GLib.Variant("s", uri)):
                        return GLib.SOURCE_REMOVE

                attempt[0] += 1
                if attempt[0] >= 40:
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE

            GLib.timeout_add(_TAB_WAIT_MS, _wait_for_tab)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_fire_and_wait)

    def _do_open_window(self, mountpoint: str) -> None:
        subprocess.Popen(["nautilus", "--new-window", mountpoint])

    def _do_mount(self, m: MountInfo, win: Gtk.Window) -> None:
        if not m or not m.gio_volume or not m.can_mount:
            return
        op = Gio.MountOperation.new()
        m.gio_volume.mount(Gio.MountMountFlags.NONE, op, None, self._on_mount_finish, win)

    def _on_mount_finish(self, volume, result, win) -> None:
        try:
            volume.mount_finish(result)
        except GLib.Error as e:
            _log(f"mount failed: {e.message}")
        GLib.idle_add(self._repopulate_visible)

    def _do_mount_then_open(self, m: MountInfo, win: Gtk.Window, mode: str) -> None:
        if not m or not m.gio_volume or not m.can_mount:
            return
        op = Gio.MountOperation.new()
        op.set_password_save(Gio.PasswordSave.NEVER)
        m.gio_volume.mount(
            Gio.MountMountFlags.NONE, op, None, self._on_mount_then_open_finish, (win, mode)
        )

    def _on_mount_then_open_finish(self, volume, result, user_data) -> None:
        win, mode = user_data
        try:
            volume.mount_finish(result)
        except GLib.Error as e:
            _log(f"mount-then-open failed: {e.message}")
            GLib.idle_add(self._repopulate_visible)
            return
        mount = volume.get_mount()
        if not mount:
            GLib.idle_add(self._repopulate_visible)
            return
        uri = mount.get_root().get_uri()
        GLib.idle_add(self._repopulate_visible)
        if mode == "tab":
            GLib.idle_add(self._do_open_tab, uri, win)
        elif mode == "window":
            GLib.idle_add(self._do_open_window, uri)
        else:
            GLib.idle_add(self._do_open, uri, win)

    def _do_unmount(self, m: MountInfo) -> None:
        if not m or not m.gio_mount or not m.can_unmount:
            return
        op = Gio.MountOperation.new()
        m.gio_mount.unmount_with_operation(
            Gio.MountUnmountFlags.NONE, op, None, self._on_unmount_finish
        )

    def _on_unmount_finish(self, mount, result) -> None:
        try:
            mount.unmount_with_operation_finish(result)
        except GLib.Error as e:
            _log(f"unmount failed: {e.message}")
        GLib.idle_add(self._repopulate_visible)

    def _do_eject(self, m: MountInfo) -> None:
        if not m:
            return
        op = Gio.MountOperation.new()
        if m.gio_volume and m.gio_volume.can_eject():
            m.gio_volume.eject_with_operation(
                Gio.MountUnmountFlags.NONE, op, None, self._on_eject_finish
            )
        elif m.gio_mount and m.gio_mount.can_eject():
            m.gio_mount.eject_with_operation(
                Gio.MountUnmountFlags.NONE, op, None, self._on_eject_finish
            )

    def _on_eject_finish(self, source, result) -> None:
        try:
            source.eject_with_operation_finish(result)
        except GLib.Error as e:
            _log(f"eject failed: {e.message}")
        GLib.idle_add(self._repopulate_visible)

    def _do_format(self, device: str) -> None:
        try:
            Gio.Subprocess.new(
                ["gnome-disks", "--block-device", device, "--format-device"],
                Gio.SubprocessFlags.NONE,
            )
        except GLib.Error as e:
            _log(f"format launch failed: {e.message}")

    def _do_properties(self, nav_uri: str, win: Gtk.Window) -> None:
        uri = nav_uri

        # The native properties window is created in-process by Nautilus via the
        # D-Bus ShowItemProperties call. It is NOT registered with the
        # GtkApplication, so "window-added" never fires — we must poll
        # list_toplevels() to find it. Once found, set it transient-for our
        # window and modal so the compositor visually binds it to the parent
        # (centered, above, moves/closes with it) instead of floating free.
        before_ids = {id(w) for w in Gtk.Window.list_toplevels()}
        state = {"done": False}

        def _try_parent(attempt=0):
            if state["done"]:
                return GLib.SOURCE_REMOVE
            for w in Gtk.Window.list_toplevels():
                if id(w) not in before_ids and w is not win:
                    w.set_transient_for(win)
                    w.set_modal(True)
                    state["done"] = True
                    return GLib.SOURCE_REMOVE
            if attempt < 40:
                GLib.timeout_add(25, lambda: _try_parent(attempt + 1))
            return GLib.SOURCE_REMOVE

        def _on_call(bus, result, _):
            try:
                bus.call_finish(result)
            except Exception:
                pass

        def _on_bus(_, result):
            try:
                bus = Gio.bus_get_finish(result)
                bus.call(
                    DBUS_FILE_MANAGER,
                    DBUS_PATH_FILE_MANAGER,
                    DBUS_FILE_MANAGER,
                    "ShowItemProperties",
                    GLib.Variant("(ass)", ([uri], "")),
                    None,
                    Gio.DBusCallFlags.NONE,
                    5000,
                    None,
                    _on_call,
                    None,
                )
            except Exception:
                pass

        # Start polling immediately so we catch the window as early as possible.
        _try_parent()
        Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)

    def _launch_settings_panel(self, panel: str) -> None:
        """Open a gnome-control-center panel (e.g. 'privacy'), matching native rows."""
        try:
            Gio.Subprocess.new(["gnome-control-center", panel], Gio.SubprocessFlags.NONE)
        except GLib.Error as e:
            _log(f"settings launch failed ({panel}): {e.message}")

    def _launch_prefs(self, win: Gtk.Window | None = None) -> None:
        if not self._gsettings:
            return

        detached = DETACH_SETTINGS_WINDOW or win is None
        pref_win = Adw.PreferencesWindow() if detached else Adw.PreferencesDialog()
        pref_win.set_title(PREFS_WIN_TITLE)
        pref_win.set_search_enabled(False)
        if detached:
            pref_win.set_default_size(680, 760)

        page = Adw.PreferencesPage()
        pref_win.add(page)

        gen_group = Adw.PreferencesGroup()
        gen_group.set_title(_("General"))
        page.add(gen_group)

        start_row = Adw.SwitchRow()
        start_row.set_title(_("Start on the Computer view"))
        start_row.set_subtitle(_("Launch directly to the Computer view instead of Home"))
        self._gsettings.bind("start-on-disks", start_row, "active", Gio.SettingsBindFlags.DEFAULT)
        gen_group.add(start_row)

        vis_group = Adw.PreferencesGroup()
        vis_group.set_title(_("Panel visibility"))
        vis_group.set_description(
            _(
                "Choose how each group appears. "
                "Visible: shows the group as a normal separated section. "
                "Merged: folds the group into the On this Computer group. "
                "Hidden: hides the group entirely."
            )
        )
        page.add(vis_group)

        _vis_map = ["visible", "merged", "hidden"]
        _vis_labels = [_("Visible"), _("Merged"), _("Hidden")]
        _folders_vis_map = ["visible", "hidden"]
        _folders_vis_labels = [_("Visible"), _("Hidden")]

        folders_combo = Adw.ComboRow()
        folders_combo.set_title(_("Preferred Folders"))
        folders_combo.set_model(Gtk.StringList.new(_folders_vis_labels))
        current_folders_vis = self._gsettings.get_string("visibility-preferred-folders")
        folders_combo.set_selected(
            _folders_vis_map.index(current_folders_vis)
            if current_folders_vis in _folders_vis_map
            else 0
        )

        def _on_folders_vis_changed(c, _param):
            idx = c.get_selected()
            if 0 <= idx < len(_folders_vis_map):
                self._gsettings.set_string("visibility-preferred-folders", _folders_vis_map[idx])

        folders_combo.connect("notify::selected", _on_folders_vis_changed)
        vis_group.add(folders_combo)

        for gkey, glabel, gskey in _GROUP_SPEC:
            if gskey is None:
                continue  # "On this Computer" is always visible -- no control needed

            combo = Adw.ComboRow()
            combo.set_title(_(glabel))
            combo.set_model(Gtk.StringList.new(_vis_labels))
            current = self._gsettings.get_string(gskey)
            combo.set_selected(_vis_map.index(current) if current in _vis_map else 0)

            def _on_vis_changed(c, _param, _gskey=gskey):
                idx = c.get_selected()
                if 0 <= idx < len(_vis_map):
                    self._gsettings.set_string(_gskey, _vis_map[idx])

            combo.connect("notify::selected", _on_vis_changed)
            vis_group.add(combo)

        show_sys_parts_row = Adw.SwitchRow()
        show_sys_parts_row.set_title(_("Show system partitions"))
        show_sys_parts_row.set_subtitle(_("Include boot and EFI partitions in the System group"))
        self._gsettings.bind(
            "show-system-partitions", show_sys_parts_row, "active", Gio.SettingsBindFlags.DEFAULT
        )
        vis_group.add(show_sys_parts_row)

        sidebar_vis_group = Adw.PreferencesGroup()
        sidebar_vis_group.set_title(_("Sidebar visibility"))
        sidebar_vis_group.set_description(_("Choose which locations appear on the sidebar."))
        page.add(sidebar_vis_group)

        # One toggle per native place (Computer is always shown, no key, not here).
        for entry in NATIVE_PLACES:
            gskey = _PLACE_VISIBILITY_KEYS[entry.name]
            place_row = Adw.SwitchRow()
            place_row.set_title(entry.label)
            icon_img = Gtk.Image.new_from_icon_name(entry.icon)
            icon_img.set_icon_size(Gtk.IconSize.NORMAL)
            place_row.add_prefix(icon_img)
            self._gsettings.bind(gskey, place_row, "active", Gio.SettingsBindFlags.DEFAULT)
            sidebar_vis_group.add(place_row)

        color_group = Adw.PreferencesGroup()
        color_group.set_title(_("Bar Color"))
        color_group.set_description(_("Select or customize the bar color."))
        page.add(color_group)

        mode_row = Adw.ComboRow()
        mode_row.set_title(_("Color mode"))
        mode_model = Gtk.StringList.new(
            [_("System accent"), _("Custom color"), _("Custom gradient")]
        )
        mode_row.set_model(mode_model)
        _mode_map = ["accent", "flat", "gradient"]
        current_mode = self._gsettings.get_string("color-mode")
        mode_row.set_selected(_mode_map.index(current_mode) if current_mode in _mode_map else 0)
        color_group.add(mode_row)

        color_dialog = Gtk.ColorDialog()
        color_dialog.set_with_alpha(False)

        def _hex_to_rgba(hex_str: str) -> Gdk.RGBA:
            rgba = Gdk.RGBA()
            rgba.parse(hex_str)
            return rgba

        def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
            r = int(rgba.red * 255)
            g = int(rgba.green * 255)
            b = int(rgba.blue * 255)
            return f"#{r:02X}{g:02X}{b:02X}"

        flat_row = Adw.ActionRow()
        flat_row.set_title(_("Color"))
        flat_btn = Gtk.ColorDialogButton(dialog=color_dialog)
        flat_btn.set_valign(Gtk.Align.CENTER)
        flat_btn.set_rgba(_hex_to_rgba(self._gsettings.get_string("custom-color")))
        flat_btn.connect(
            "notify::rgba",
            lambda btn, _: self._gsettings.set_string("custom-color", _rgba_to_hex(btn.get_rgba())),
        )
        flat_row.add_suffix(flat_btn)
        color_group.add(flat_row)

        grad_row1 = Adw.ActionRow()
        grad_row1.set_title(_("Start color"))
        grad_btn1 = Gtk.ColorDialogButton(dialog=color_dialog)
        grad_btn1.set_valign(Gtk.Align.CENTER)
        grad_btn1.set_rgba(_hex_to_rgba(self._gsettings.get_string("custom-gradient-color-1")))
        grad_btn1.connect(
            "notify::rgba",
            lambda btn, _: self._gsettings.set_string(
                "custom-gradient-color-1", _rgba_to_hex(btn.get_rgba())
            ),
        )
        grad_row1.add_suffix(grad_btn1)
        color_group.add(grad_row1)

        grad_row2 = Adw.ActionRow()
        grad_row2.set_title(_("End color"))
        grad_btn2 = Gtk.ColorDialogButton(dialog=color_dialog)
        grad_btn2.set_valign(Gtk.Align.CENTER)
        grad_btn2.set_rgba(_hex_to_rgba(self._gsettings.get_string("custom-gradient-color-2")))
        grad_btn2.connect(
            "notify::rgba",
            lambda btn, _: self._gsettings.set_string(
                "custom-gradient-color-2", _rgba_to_hex(btn.get_rgba())
            ),
        )
        grad_row2.add_suffix(grad_btn2)
        color_group.add(grad_row2)

        def _update_color_rows(selected: int) -> None:
            flat_row.set_visible(selected == 1)
            grad_row1.set_visible(selected == 2)
            grad_row2.set_visible(selected == 2)

        def _on_mode_changed(row, _) -> None:
            idx = row.get_selected()
            self._gsettings.set_string("color-mode", _mode_map[idx])
            _update_color_rows(idx)

        mode_row.connect("notify::selected", _on_mode_changed)
        _update_color_rows(mode_row.get_selected())

        about_group = Adw.PreferencesGroup()
        about_group.set_title(_("About"))
        page.add(about_group)

        def _about_row(title: str, value: str) -> Adw.ActionRow:
            row = Adw.ActionRow()
            row.set_title(title)
            lbl = Gtk.Label(label=value)
            lbl.get_style_context().add_class("dim-label")
            lbl.set_valign(Gtk.Align.CENTER)
            row.add_suffix(lbl)
            return row

        about_group.add(_about_row(_("Extension"), EXT_NAME))
        about_group.add(_about_row(_("Version"), EXT_VERSION))
        about_group.add(_about_row(_("Author"), EXT_AUTHOR))
        about_group.add(_about_row(_("License"), EXT_LICENSE))

        github_row = Adw.ActionRow()
        github_row.set_title(_("Source code"))
        github_btn = Gtk.LinkButton(uri=EXT_GITHUB, label=_("GitHub"))
        github_btn.get_style_context().add_class("flat")
        github_btn.set_valign(Gtk.Align.CENTER)
        github_row.add_suffix(github_btn)
        about_group.add(github_row)

        if detached:
            pref_win.present()
        else:
            pref_win.present(win)

    def _navigate_to_disks(self, win: Gtk.Window) -> None:
        """Navigate a window to computer:/// at startup, retrying until the slot
        is ready. The slot often isn't navigable the instant the window settles
        on Home, so a single open-location call silently no-ops; we retry on a
        short bounded poll and stop as soon as the location actually changes.
        While awaiting arrival the panel stays pinned (see _on_title_changed)."""
        state = self._windows.get(win)
        if state is not None:
            state["awaiting_disks"] = True
            self._blank_vanilla_view(state, True)

        attempts = [0]

        def _try() -> bool:
            st = self._windows.get(win)
            if st is None:
                return GLib.SOURCE_REMOVE
            if _window_is_at_disks(win):
                st["awaiting_disks"] = False  # arrived
                return GLib.SOURCE_REMOVE
            attempts[0] += 1
            if attempts[0] > 25:  # ~1.5 s budget, then give up
                st["awaiting_disks"] = False
                self._blank_vanilla_view(st, False)
                return GLib.SOURCE_REMOVE
            self._navigate_to(DISKS_URI, win)
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(_NAV_RETRY_MS, _try)

    def _navigate_to(self, uri: str, win: Gtk.Window) -> bool:
        state = self._windows.get(win) if uri == DISKS_URI else None

        def _arm_blank() -> None:
            if state is not None:
                self._blank_vanilla_view(state, True)

        for w in _all_widgets(win):
            if "Slot" in type(w).__name__:
                try:
                    if w.activate_action("open-location", GLib.Variant("s", uri)):
                        _arm_blank()
                        return False
                except Exception:
                    pass
        try:
            if win.activate_action("slot.open-location", GLib.Variant("s", uri)):
                _arm_blank()
                return False
        except Exception:
            pass

        def _on_proxy(_, result):
            try:
                proxy = Gio.DBusProxy.new_for_bus_finish(result)
                proxy.call(
                    "ShowFolders",
                    GLib.Variant("(ass)", ([uri], "")),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                    None,
                )
            except Exception:
                pass

        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            DBUS_FILE_MANAGER,
            DBUS_PATH_FILE_MANAGER,
            DBUS_FILE_MANAGER,
            None,
            _on_proxy,
        )
        return False

    # ── Chrome icon fix (path bar chip) ─────────────────────────────────────

    def _find_sidebar_listbox(self, nautilus_sidebar) -> Gtk.ListBox | None:
        places_sidebar = None
        for w in _all_widgets(nautilus_sidebar):
            buildable_id = w.get_buildable_id() if hasattr(w, "get_buildable_id") else None
            widget_name = w.get_name() if hasattr(w, "get_name") else None
            if buildable_id == "places_sidebar" or widget_name == "places_sidebar":
                places_sidebar = w
                break
        if places_sidebar is None:
            places_sidebar = _find_widget(
                nautilus_sidebar,
                class_name="NautilusSidebar",
                site="_find_sidebar_listbox",
            )
        search_root = places_sidebar or nautilus_sidebar

        for w in _all_widgets(search_root):
            if isinstance(w, Gtk.ListBox) and w.has_css_class("navigation-sidebar"):
                return w

        for w in _all_widgets(search_root):
            if isinstance(w, Gtk.ListBox) and w.has_css_class("places-sidebar-list"):
                return w

        for w in _all_widgets(search_root):
            if isinstance(w, Gtk.ListBox):
                _log("_find_sidebar_listbox: no known sidebar class found, using first GtkListBox")
                return w
        return None

    def _build_place_sidebar_row(
        self, win: Gtk.Window, entry: PlaceEntry, nautilus_sidebar: Gtk.Widget | None = None
    ) -> Gtk.ListBoxRow:
        # Only the Computer row is built here (it has no native equivalent). Every
        # other place stays native; we just toggle its native row's visibility.
        row_label = entry.label
        row_tooltip = entry.tooltip
        icon_name = self._get_computer_icon() if entry.uri == DISKS_URI else entry.icon

        # Try to instantiate NautilusSidebarRow directly from the Nautilus GObject
        # type system. It is registered at runtime when Nautilus loads, so
        # GObject.type_from_name() can find it. uri is construct-only.
        list_row = None
        try:
            row_gtype = GObject.type_from_name("NautilusSidebarRow")
            row_props = {
                "uri": entry.uri,
                "place-type": 0,  # NAUTILUS_SIDEBAR_ROW_INVALID, sorts before built-in rows
                "section-type": 1,  # NAUTILUS_SIDEBAR_SECTION_DEFAULT_LOCATIONS
                "order-index": entry.order_index,
                "label": row_label,
                "tooltip": row_tooltip,
                "eject-tooltip": _("Unmount"),
                "start-icon": Gio.ThemedIcon.new(icon_name),
            }
            if nautilus_sidebar is not None:
                row_props["sidebar"] = nautilus_sidebar

            list_row = GObject.new(row_gtype, **row_props)
            list_row.set_name(f"place_{entry.name}")
            list_row.set_has_tooltip(True)
            _log(f"_build_place_sidebar_row: NautilusSidebarRow created (uri={entry.uri})")
        except Exception as e:
            _log(
                f"_build_place_sidebar_row: NautilusSidebarRow unavailable ({e}),"
                " using GtkListBoxRow"
            )

        if list_row is None:
            list_row = Gtk.ListBoxRow()
            list_row.set_name(f"place_{entry.name}")
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row_box.set_name(f"place_{entry.name}_box")
            icon_img = Gtk.Image.new_from_icon_name(icon_name)
            icon_img.set_name(f"place_{entry.name}_icon")
            icon_img.add_css_class("sidebar-icon")
            icon_img.set_icon_size(Gtk.IconSize.NORMAL)
            lbl = Gtk.Label(label=row_label)
            lbl.set_name(f"place_{entry.name}_label")
            lbl.add_css_class("sidebar-label")
            lbl.set_xalign(0.0)
            lbl.set_hexpand(True)
            lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            row_box.append(icon_img)
            row_box.append(lbl)
            list_row.set_child(row_box)

        list_row.add_css_class("activatable")

        def _pin_row_icon():
            for w in _all_widgets(list_row):
                if not isinstance(w, Gtk.Image):
                    continue
                parent = w.get_parent()
                in_button = False
                while parent and parent is not list_row:
                    if isinstance(parent, Gtk.Button):
                        in_button = True
                        break
                    parent = parent.get_parent()
                if not in_button:
                    _pin_icon(w, icon_name)
                    break
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_pin_row_icon)

        # Right-click context menu (Computer carries _computer_context_menu).
        if callable(entry.menu):

            def _on_place_right_clicked(gesture, _n, x, y):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                ctx_menu = entry.menu(self, win, entry)
                popover = ctx_menu.build_popover(list_row, f"place_{entry.name}")
                rect = Gdk.Rectangle()
                rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
                popover.set_pointing_to(rect)
                popover.popup()

            right_click = Gtk.GestureClick()
            right_click.set_button(3)
            right_click.connect("pressed", _on_place_right_clicked)
            list_row.add_controller(right_click)

        # Hide the eject button - not applicable for our injected entries.
        btn = _find_widget(list_row, buildable_id="eject_button")
        if isinstance(btn, Gtk.Button):
            btn.set_visible(False)

        return list_row

    def _find_sidebar_scrolled_window(
        self, nautilus_sidebar: Gtk.Widget, native_listbox: Gtk.ListBox | None = None
    ) -> Gtk.ScrolledWindow | None:
        target_listbox = native_listbox or self._find_sidebar_listbox(nautilus_sidebar)
        for w in _all_widgets(nautilus_sidebar):
            if not isinstance(w, Gtk.ScrolledWindow):
                continue
            if target_listbox is None:
                return w
            parent = target_listbox.get_parent()
            while parent is not None:
                if parent is w:
                    return w
                parent = parent.get_parent()
        return None

    def _inject_separate_computer_row(
        self,
        win: Gtk.Window,
        nautilus_sidebar: Gtk.Widget,
        native_scrolled_window: Gtk.ScrolledWindow,
        native_listbox: Gtk.ListBox,
    ) -> bool:
        """Put a one-row 'Computer' listbox ABOVE Nautilus' native list, inside
        the sidebar's scrolled window. Computer is visually its own section; it
        never enters Nautilus' managed listbox, so Nautilus rebuilds (bookmark
        drag-and-drop, mounts) never move/remove it - no flicker. The native
        places stay native; we only hide the ones toggled off via settings.

        Builds one row per entry in PLACES (currently just Computer), rather than
        hardcoding the single row, so a future custom place only needs adding to
        PLACES."""
        my_computer_listbox = Gtk.ListBox()
        my_computer_listbox.set_name("sidebar_my_computer_listbox")
        my_computer_listbox.add_css_class("navigation-sidebar")
        my_computer_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        row_uris: dict[Gtk.ListBoxRow, str] = {}
        for entry in PLACES:
            row = self._build_place_sidebar_row(win, entry, nautilus_sidebar)
            my_computer_listbox.append(row)
            row_uris[row] = entry.uri
        my_computer_listbox.connect(
            "row-activated", lambda _lb, row: self._navigate_to(row_uris.get(row, DISKS_URI), win)
        )
        computer_row = my_computer_listbox.get_row_at_index(0)

        # Wrap: the My Computer one-row list on top, the native list below, in the existing
        # scrolled window so both scroll together.
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wrapper.set_name("sidebar_my_computer_wrapper")
        native_scrolled_window.set_child(wrapper)
        wrapper.append(my_computer_listbox)
        wrapper.append(native_listbox)

        # Cross-deselect: the My Computer selection and any native section selection
        # are mutually exclusive (GTK does not deselect across separate listboxes).
        all_lbs = [my_computer_listbox] + [
            w for w in _all_widgets(nautilus_sidebar) if isinstance(w, Gtk.ListBox)
        ]
        _deselecting = [False]

        def _on_any_lb_selected(selected_lb, row):
            if _deselecting[0] or row is None:
                return
            _deselecting[0] = True
            try:
                for lb in all_lbs:
                    if lb is not selected_lb:
                        lb.unselect_all()
            finally:
                _deselecting[0] = False

        for lb in all_lbs:
            lb.connect("row-selected", _on_any_lb_selected)

        state = self._windows.get(win)
        if state is not None:
            state["sidebar_listbox"] = native_listbox
            state["sidebar_my_computer_listbox"] = my_computer_listbox
            state["sidebar_row"] = computer_row
            state["sidebar_native"] = True
            state["sidebar_native_widget"] = nautilus_sidebar

        # Hide native place rows the user toggled off, and re-apply on rebuilds.
        self._apply_native_place_visibility(native_listbox)
        self._attach_bookmark_context_menus(win, native_listbox)
        self._apply_bookmark_icons(native_listbox)
        self._watch_native_list_changes(win, native_listbox)

        self._wire_computer_drop_dimming(wrapper, computer_row)
        return True

    def _wire_computer_drop_dimming(self, area: Gtk.Widget, computer_row: Gtk.ListBoxRow) -> None:
        """Grey out (desensitize) the Computer row while a file drag is over the
        sidebar, matching Nautilus' own invalid-drop-target feedback. Computer
        has no Gtk.DropTarget of its own (it is not a real folder, like
        recent:/// or starred:///), so a desensitized row simply never receives
        pointer/drop events - that IS the rejection mechanism, not a DropTarget
        that claims and refuses the drag.

        Latches to the Gdk.Drag's lifetime rather than raw enter/leave: a child
        widget's own Gtk.DropTarget (Nautilus' native listbox has one) steals
        the drop and fires a spurious `leave` on this controller as the pointer
        crosses into it, which would flicker the dimming off. See
        tmp/Logs/2026-06-16 - 1719 - [Feature] Drag-and-drop sidebar visual
        feedback.md for the full investigation - this is the proven fix."""
        drag_state = {"drag": None}

        def _set_dimmed(dimmed: bool) -> None:
            computer_row.set_sensitive(not dimmed)

        def _undim(*_a):
            drag_state["drag"] = None
            _set_dimmed(False)

        def _on_enter(controller, *_a):
            _set_dimmed(True)
            if drag_state["drag"] is not None:
                return
            drop = controller.get_drop()
            drag = drop.get_drag() if drop is not None else None
            if drag is None:
                return
            drag_state["drag"] = drag
            drag.connect("dnd-finished", _undim)
            drag.connect("cancel", lambda *_a: _undim())

        def _on_leave(controller, *_a):
            def _check():
                if not controller.contains_pointer():
                    _set_dimmed(False)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_check)

        motion = Gtk.DropControllerMotion.new()
        motion.connect("enter", _on_enter)
        motion.connect("motion", lambda *_a: _set_dimmed(True))
        motion.connect("leave", _on_leave)
        area.add_controller(motion)

    def _watch_native_list_changes(self, win: Gtk.Window, native_listbox: Gtk.ListBox) -> None:
        """Re-apply native place visibility whenever Nautilus mutates the list.

        `observe_children()` returns a live GListModel of the listbox's child
        rows; its items-changed fires on add, remove AND reorder. Because we set
        row visibility with `set_visible()` (a property Nautilus can overwrite
        when it rebuilds a row), a one-shot pass is not enough - this watcher
        re-applies it.

        Changes arrive in bursts (Nautilus rebuilds several rows at once), so we
        coalesce into a single GLib.idle_add pass via a per-window pending flag
        (idle, not a timeout - no polling)."""
        state = self._windows.get(win)
        if state is None:
            return
        model = native_listbox.observe_children()

        def _rescan() -> bool:
            state["native_hide_pending"] = False
            self._apply_native_place_visibility(native_listbox)
            self._attach_bookmark_context_menus(win, native_listbox)
            self._apply_bookmark_icons(native_listbox)
            return GLib.SOURCE_REMOVE

        def _on_items_changed(*_a) -> None:
            if state.get("native_hide_pending"):
                return
            state["native_hide_pending"] = True
            GLib.idle_add(_rescan)

        handler_id = model.connect("items-changed", _on_items_changed)
        # Keep refs alive for the window's lifetime so the model is not collected.
        state["native_hide_model"] = model
        state["native_hide_handler"] = handler_id

    def _gtk_bookmark_uris(self) -> set:
        return bookmarks.bookmark_uris()

    def _get_bookmark_icons(self) -> dict:
        return bookmarks.get_bookmark_icons(self._gsettings)

    def _set_bookmark_icon(self, uri: str, icon_name: str) -> None:
        bookmarks.set_bookmark_icon(self._gsettings, uri, icon_name)

    def _clear_bookmark_icon(self, uri: str) -> None:
        bookmarks.clear_bookmark_icon(self._gsettings, uri)

    def _apply_bookmark_icons(self, native_listbox: Gtk.ListBox) -> None:
        bookmarks.apply_bookmark_icons(self, native_listbox)

    def _reapply_bookmark_icons_all_windows(self) -> bool:
        return bookmarks.reapply_bookmark_icons_all_windows(self)

    def _attach_bookmark_context_menus(self, win: Gtk.Window, native_listbox: Gtk.ListBox) -> None:
        bookmarks.attach_bookmark_context_menus(self, win, native_listbox)

    # ── Preferred Folders (issue #30) ───────────────────────────────────────────

    def _get_preferred_folders(self) -> list:
        return preferred_folders.get_preferred_entries(self._gsettings)

    def _add_preferred_folder(self, uri: str) -> None:
        preferred_folders.add_preferred(self._gsettings, uri)

    def _do_remove_preferred_folder(self, pf: "PreferredFolder", win: Gtk.Window) -> None:
        preferred_folders.remove_preferred(self._gsettings, pf.key)

    def _commit_preferred_order(self, keys: list[str]) -> bool:
        """Persist a drag-reordered Preferred Folders sequence. Writing the
        gsettings key fires _on_settings_changed -> _repopulate_visible, which
        rebuilds the cards from the saved order (clearing any drag-preview
        state). Called via GLib.idle_add from the drop handler so the rebuild
        happens after the drag has fully torn down."""
        preferred_folders.save_order(self._gsettings, keys)
        return GLib.SOURCE_REMOVE

    # ── "Pin to My Computer" injection (issue #30) ────────────────────────────

    def _attach_pathbar_menu_watch(self, win: Gtk.Window) -> None:
        preferred_folders.attach_pathbar_menu_watch(self, win)

    def _open_bookmark_icon_picker(self, uri: str, label: str, row) -> None:
        bookmarks.open_bookmark_icon_picker(self, uri, label, row)

    def _apply_native_place_visibility(self, native_listbox: Gtk.ListBox) -> None:
        """Instance shim so call sites can use self; delegates to the helper."""
        _apply_native_place_visibility(native_listbox, self._gsettings)

    def _reapply_sidebar_visibility(self) -> bool:
        """Re-apply native place visibility in every window after a settings change."""
        for _win, state in list(self._windows.items()):
            native_listbox = state.get("sidebar_listbox")
            if native_listbox is not None:
                self._apply_native_place_visibility(native_listbox)
        return GLib.SOURCE_REMOVE

    def _inject_sidebar_link(self, win: Gtk.Window) -> bool:
        """Inject a separate one-row 'Computer' section above Nautilus' native
        sidebar list. Computer lives in its own listbox (never in Nautilus'
        managed list), so Nautilus rebuilds never move it. Every other place
        stays native; we only hide the ones toggled off via settings.
        """
        split_view = next(
            (w for w in _all_widgets(win) if isinstance(w, Adw.OverlaySplitView)), None
        )
        sidebar_toolbar = split_view.get_sidebar() if split_view else None
        if not isinstance(sidebar_toolbar, Adw.ToolbarView):
            _log(
                f"_inject_sidebar_link: expected AdwToolbarView from get_sidebar(), "
                f"got {type(sidebar_toolbar).__name__ if sidebar_toolbar else 'None'}"
            )
            return False

        nautilus_sidebar = sidebar_toolbar.get_content()
        if nautilus_sidebar is None:
            _log("_inject_sidebar_link: AdwToolbarView content is None")
            return False

        _log(f"_inject_sidebar_link: content={type(nautilus_sidebar).__name__}")

        native_listbox = self._find_sidebar_listbox(nautilus_sidebar)
        if native_listbox is None:
            _log("_inject_sidebar_link: native listbox unavailable")
            return False

        native_scrolled_window = self._find_sidebar_scrolled_window(
            nautilus_sidebar, native_listbox
        )
        if native_scrolled_window is None:
            _log("_inject_sidebar_link: native scrolled window unavailable")
            return False

        # Guard: skip if we already wrapped this sidebar (double-injection).
        existing = native_scrolled_window.get_child()
        if existing is not None and existing.get_name() == "sidebar_my_computer_wrapper":
            _log("_inject_sidebar_link: wrapper already present, skipping")
            return True

        return self._inject_separate_computer_row(
            win, nautilus_sidebar, native_scrolled_window, native_listbox
        )

    def _fix_pathbar_icon(self, win: Gtk.Window) -> bool:
        """Non-invasive chip icon update. Called on each title-change arrival at
        computer:///. Scans the window for the chip label, finds the existing
        Gtk.Image in the chip, and pins it to computer-symbolic.

        Never connects signals to Nautilus's internal pathbar GtkStack or box
        models, and never calls set_child() — those caused the GTK_IS_STACK crash
        (issue #11). The notify::title trigger already fires on every navigation,
        so no persistent watcher is needed."""
        target_labels = {COMPUTER_LABEL, _LOCATION_TITLE}

        for w in _all_widgets(win):
            if not isinstance(w, Gtk.Label):
                continue
            label_text = w.get_label()
            if not label_text or label_text.strip() not in target_labels:
                continue

            # Skip labels inside the sidebar
            ancestor = w.get_parent()
            in_sidebar = False
            while ancestor:
                cls = type(ancestor).__name__
                if "Sidebar" in cls or "PlacesView" in cls:
                    in_sidebar = True
                    break
                if cls in ("NautilusPathBarButton", "GtkButton", "AdwButton"):
                    break
                ancestor = ancestor.get_parent()
            if in_sidebar:
                continue

            # Walk up to the chip container
            container = w.get_parent()
            while container and type(container).__name__ not in (
                "NautilusPathBarButton",
                "GtkButton",
                "GtkBox",
                "Button",
                "Box",
            ):
                container = container.get_parent()

            if not container:
                continue

            # Pin the existing chip image — no structural changes to Nautilus's tree
            for sub in _all_widgets(container):
                if isinstance(sub, Gtk.Image):
                    _pin_icon(sub, self._get_computer_icon())
                    break

        return False

    # ── MenuProvider ─────────────────────────────────────────────────────────
    # get_file_items() is (ab)used purely as Nautilus's official, reliable feed
    # of "what's currently selected" -- it fires on every selection change, and
    # nautilus_files_view_pop_up_selection_context_menu() forces a pending
    # update before popping up, so the cache below is guaranteed fresh at
    # right-click time. We do NOT return items through this API: every
    # extension's get_file_items() results land in one shared, separator-less
    # GMenu section (selection-extensions-section), so our two lines could end
    # up jammed against another extension's with no visual break. Instead we
    # inject directly into the native selection popover (see
    # _attach_file_view_context_menu below), exactly like the existing
    # bookmark-row and pathbar injections, which gives full control over
    # placement and a real separator.

    def get_file_items(self, *args):
        files = args[-1] if args else []
        self._last_selected_folder_uri = None
        if len(files) == 1 and files[0].is_directory():
            self._last_selected_folder_uri = files[0].get_uri()
        return []

    def get_background_items(self, *args):
        return []

    # ── Folder selection: Bookmarks/Preferred injection (native file view) ──

    def _attach_file_view_context_menu(self, win: Gtk.Window) -> None:
        file_view_menu.attach_file_view_context_menu(self, win)
