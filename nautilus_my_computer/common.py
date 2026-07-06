"""Stateless leaf utilities shared by main.py, target modules, and widgets.py.

No app state, no GSettings here -- only pure functions and constants so this
module can be imported from anywhere without import cycles. Includes the
generic native-widget/menu-model primitives (tree walking, menu-section
lookup, icon pinning) used by every native-UI injection target.
"""

import gettext
import os

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

_custom_translation = None
_localedir = os.path.expanduser("~/.local/share/locale")
try:
    _custom_translation = gettext.translation("nautilus-my-computer", localedir=_localedir)
except Exception:
    pass

_nautilus_translation = None
try:
    _nautilus_translation = gettext.translation("nautilus")
except Exception:
    pass


def _(text: str) -> str:
    if _custom_translation is not None:
        val = _custom_translation.gettext(text)
        if val != text:
            return val
    if _nautilus_translation is not None:
        return _nautilus_translation.gettext(text)
    return text


def _format_size(n: float) -> str:
    return GLib.format_size(int(n))


def _gicon_renders(gicon) -> bool:
    """True if gicon is non-None and resolves in the current icon theme."""
    if gicon is None:
        return False
    if isinstance(gicon, Gio.ThemedIcon):
        try:
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        except Exception:
            return True
        return any(theme.has_icon(n) for n in gicon.get_names())
    return True


def _icon_name_renders(icon_name: str) -> bool:
    """True if icon_name resolves in the current icon theme."""
    try:
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    except Exception:
        return True
    return theme.has_icon(icon_name)


def _uri_is_hidden(uri: str) -> bool:
    """True if the location's standard::is-hidden attribute is set.

    Local stat only -- callers must not use this on a GVfs/network URI without
    a local FUSE path, since query_info() on those can block on the network.
    """
    if not uri:
        return False
    try:
        info = Gio.File.new_for_uri(uri).query_info(
            "standard::is-hidden", Gio.FileQueryInfoFlags.NONE, None
        )
        return info.get_attribute_boolean("standard::is-hidden")
    except GLib.Error:
        return False


DEBUG_LOG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
DEBUG_LOG_PREFIX = "MyComputer"  # prefix for all debug lines, to make them easy to filter in logs


def _log(msg: str) -> None:
    """Print a prefixed debug line. Set DEBUG_LOG = False to silence all logs."""
    if DEBUG_LOG:
        print(f"{DEBUG_LOG_PREFIX}: {msg}", flush=True)


def _all_widgets(widget):
    """Depth-first walk of widget and every descendant (widget itself first)."""
    if not widget:
        return
    yield widget
    # Using observe_children instead of get_first_child/get_next_sibling
    # is safer in some GTK4 contexts but let's stick to the basic tree walker.
    child = widget.get_first_child()
    while child:
        yield from _all_widgets(child)
        child = child.get_next_sibling()


def _find_widget(root, *, buildable_id=None, class_name=None, css_class=None, site=""):
    """Find a widget by layered fallback: buildable_id → class_name → css_class.

    Rejects GtkBuilder auto-placeholders (___object_N___). Logs drift when falling
    back past tier 1 so Nautilus API changes surface without breaking the extension.
    """
    tier1 = tier2 = tier3 = None
    for w in _all_widgets(root):
        if tier1 is None and buildable_id is not None:
            bid = w.get_buildable_id() if hasattr(w, "get_buildable_id") else None
            if bid and bid == buildable_id and not bid.startswith("___object_"):
                tier1 = w
        if tier2 is None and class_name is not None:
            if type(w).__name__ == class_name:
                tier2 = w
        if tier3 is None and css_class is not None:
            if hasattr(w, "has_css_class") and w.has_css_class(css_class):
                tier3 = w
        if tier1 is not None:
            break
    result = tier1 or tier2 or tier3
    if result is not None and result is not tier1 and buildable_id is not None and site:
        tier_name = "css_class" if result is tier3 else "class_name"
        _log(f"{site}: buildable_id {buildable_id!r} not found, matched via {tier_name}")
    elif result is None and site:
        _log(f"{site}: no match (id={buildable_id!r} class={class_name!r} css={css_class!r})")
    return result


def _menu_section_with_action(model, action_name):
    """Return the section GMenu of `model` that contains an item bound to
    `action_name`, or None. Used to append into a native menu's existing group
    (e.g. the Remove/Rename section) rather than tacking on a new section."""
    str_type = GLib.VariantType.new("s")
    for i in range(model.get_n_items()):
        section = model.get_item_link(i, Gio.MENU_LINK_SECTION)
        if section is None:
            continue
        for j in range(section.get_n_items()):
            av = section.get_item_attribute_value(j, "action", str_type)
            if av is not None and av.get_string() == action_name:
                return section
    return None


def _menu_item_index(section, action_name):
    """Return the index of the item bound to `action_name` within `section`,
    or None. Used to insert right after a specific native item (e.g. directly
    below "Add to Bookmarks") instead of appending to the end of the section."""
    str_type = GLib.VariantType.new("s")
    for j in range(section.get_n_items()):
        av = section.get_item_attribute_value(j, "action", str_type)
        if av is not None and av.get_string() == action_name:
            return j
    return None


def _menu_section_index_with_action(model, action_name):
    """Return the index, within `model` itself, of the top-level section that
    contains an item bound to `action_name`, or None. Used to insert a whole
    new section right before/after an existing one (e.g. right before the
    trailing Properties section), unlike _menu_item_index which indexes
    within a section."""
    str_type = GLib.VariantType.new("s")
    for i in range(model.get_n_items()):
        section = model.get_item_link(i, Gio.MENU_LINK_SECTION)
        if section is None:
            continue
        for j in range(section.get_n_items()):
            av = section.get_item_attribute_value(j, "action", str_type)
            if av is not None and av.get_string() == action_name:
                return i
    return None


def _pin_icon(img: Gtk.Image, icon_name: str) -> None:
    """Set img's icon and keep it locked against Nautilus's async overwrites.

    Nautilus may overwrite the icon via set_from_icon_name(), set_from_gicon(),
    or set_from_paintable().  We watch all three relevant notify signals.

    Subtle bug avoided: after set_from_gicon(), get_icon_name() can still
    return the *stale* previous icon name while the displayed icon has already
    changed to the GVfs one.  We therefore also check get_gicon() to detect
    that case.  A simple boolean flag prevents re-entrance (handler_block_by_func
    has cross-signal edge-cases when one function is connected to multiple
    signals simultaneously).

    The target icon is stored as img._diskinfo_pin_name so _repin_icon() can
    update it without reconnecting signal handlers.
    """
    img._diskinfo_pin_name = icon_name
    img.set_from_icon_name(icon_name)
    img.set_visible(True)
    if getattr(img, "_diskinfo_pinned", False):
        return  # already watching — _diskinfo_pin_name update above is enough
    img._diskinfo_pinned = True
    img._diskinfo_restoring = False

    def _on_changed(image: Gtk.Image, _pspec) -> None:
        if image._diskinfo_restoring:
            return  # we triggered this notification ourselves – skip
        target = getattr(image, "_diskinfo_pin_name", None)
        if target is None:
            return
        # Detect overwrite: storage type not ICON_NAME, wrong name, or visibility dropped.
        if (
            getattr(image, "get_storage_type", lambda: None)() != Gtk.ImageType.ICON_NAME
            or image.get_icon_name() != target
            or not image.get_visible()
        ):
            image._diskinfo_restoring = True
            image.set_from_icon_name(target)
            image.set_visible(True)
            image._diskinfo_restoring = False

    img.connect("notify::icon-name", _on_changed)
    img.connect("notify::gicon", _on_changed)
    img.connect("notify::paintable", _on_changed)
    img.connect("notify::storage-type", _on_changed)
    img.connect("notify::visible", _on_changed)


def _repin_icon(img: Gtk.Image, icon_name: str) -> None:
    """Change the pinned icon target on an already-pinned Gtk.Image.
    The existing signal handlers read _diskinfo_pin_name dynamically, so
    updating the attribute and setting the new icon is all that is needed."""
    img._diskinfo_pin_name = icon_name
    img._diskinfo_restoring = True
    img.set_from_icon_name(icon_name)
    img.set_visible(True)
    img._diskinfo_restoring = False


def _find_row_start_image(row: Gtk.Widget) -> Gtk.Image | None:
    """Find a NautilusSidebarRow's start-icon Gtk.Image, skipping the eject
    button's image (same in_button walk as _build_place_sidebar_row's
    _pin_row_icon)."""
    for w in _all_widgets(row):
        if not isinstance(w, Gtk.Image):
            continue
        parent = w.get_parent()
        in_button = False
        while parent and parent is not row:
            if isinstance(parent, Gtk.Button):
                in_button = True
                break
            parent = parent.get_parent()
        if not in_button:
            return w
    return None


_ZOOM_TO_PX = {"small": 48, "small-plus": 64, "medium": 96, "large": 168, "extra-large": 256}


def _nautilus_icon_size() -> int:
    try:
        settings = Gio.Settings.new("org.gnome.nautilus.icon-view")
        zoom = settings.get_string("default-zoom-level")
        return _ZOOM_TO_PX.get(zoom, 96)
    except Exception:
        return 96


def _folder_card_width() -> int:
    """Total folder-card width (px), owned by the widget: icon width plus the
    widget's own start/end margins."""
    return _nautilus_icon_size() + _FOLDER_CARD_MARGIN_START + _FOLDER_CARD_MARGIN_END


# ── Card geometry constants ───────────────────────────────────────────────────
_DISK_ICON_SIZE = 64  # disk cards aren't native grid cells; keep our own fixed icon size
_FLOW_COLS_GRID = 8  # max columns in grid (FlowBox) view
_CARD_WIDTH = 280  # disk grid card width cap (px); beyond this,
# the grid gains another column instead of stretching cards further
_LIST_BAR_MAX_WIDTH = 240  # max width (px) of the usage bar at the end of a list-view row
_DISK_CARD_SPACING = 16  # disk card FlowBox column spacing (px)
_DISK_CARD_ROW_SPACING = 6  # disk card FlowBox row spacing (px)
_DISK_CARD_ICON_SPACING = 18  # gap between icon and details column inside a grid disk card (px)
_DISK_CARD_MARGIN_START = 8  # disk card own start inset inside its total width (px)
_DISK_CARD_MARGIN_END = 8  # disk card own end inset inside its total width (px)
_DISK_CARD_MARGIN_TOP = 6  # disk card own top inset inside its total height (px)
_DISK_CARD_MARGIN_BOTTOM = 6  # disk card own bottom inset inside its total height (px)

_FOLDER_FLOW_COLS_GRID = 20  # matches native Nautilus folder view's max column count
_FOLDER_CARD_SPACING = 24  # folder card FlowBox column spacing (px); min gap the justified
# flow stretches from once a row is full (see MyComputerJustifiedFlowBox)
_FOLDER_CARD_ROW_SPACING = 6  # folder card FlowBox row spacing (px)
_FOLDER_CARD_MARGIN_START = 8  # folder card own start inset inside its total width (px)
_FOLDER_CARD_MARGIN_END = 8  # folder card own end inset inside its total width (px)
_FOLDER_CARD_MARGIN_TOP = 8  # folder card own top inset inside its total height (px)
_FOLDER_CARD_MARGIN_BOTTOM = 4  # folder card own bottom inset inside its total height (px)

_INTERNAL_FSTYPES = {"gvfs", "unmounted", "network-place"}

# Icon per group category
_GROUP_ICON = {
    "system": "drive-harddisk",
    "local": "drive-harddisk",
    "removable": "drive-removable-media",
    "disc": "media-optical",
    "network": "folder-remote",
}
