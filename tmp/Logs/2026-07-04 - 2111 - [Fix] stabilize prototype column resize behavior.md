**Branch** `feat/miller-view-prototype`
**Version** `v0.10.0`
**Assistant** `GPT-5.4 (GitHub Copilot)`

The goal of this session was to stop the injected prototype-backed column resize repro from drifting or jumping horizontally inside Nautilus, while preserving the prototype's own resize logic as the source of truth.

### Why

The user wanted Nautilus to show the exact `tmp/column_resize_paned_repro.py` behavior, triggered from `/home/yann/Music`, without reverting to the older Miller-column implementation. The key problem was that left-resize behavior diverged from expectations in several subtle ways: the scroll position drifted, the preview column could collapse back to its floor while there was still visible empty space, and a jump appeared when a resized column reached `COLUMN_MIN_WIDTH`.

The decisive user direction was to stop adapting old column-view code and instead keep the standalone paned repro as the authoritative behavior model. That forced the implementation work to focus on preserving the prototype methods and correcting the few remaining layout and scroll edge cases in the prototype path itself.

### What

The session replaced the previous `column_view.py` implementation with a prototype-backed host that preserves the paned repro's method-level behavior, then iteratively fixed the remaining resize edge cases.

Key changes:

- Reworked `nautilus_my_computer/column_view.py` into a thin Nautilus wrapper around a `_ColumnPanedReproHost` that carries the same resize-state shape and paned-chain methods as the standalone repro.
- Kept `nautilus_my_computer/column_view_test.py` as an exact copy of `tmp/column_resize_paned_repro.py` for parity checking.
- Added a `/home/yann/Music` trigger path in `nautilus_my_computer/main.py` so the repro can be entered directly from Nautilus without reviving the removed older column-view integration path.
- Removed stale column-width constants from `nautilus_my_computer/common.py` that no longer matched the prototype-backed resize model.
- Adjusted `nautilus_my_computer/widgets.py` to align the widget-side width assumptions with the paned repro path and to avoid natural-width expansion from long labels.
- Fixed the remaining prototype resize issues:
  - limit drags at min/max width are true no-ops
  - temporary extra width is applied to the paned chain itself, not only the outer canvas
  - preview fill follows the actual visible right edge, not just the raw viewport width

### How

The main implementation strategy was to stop translating prototype behavior into a separate abstraction and instead preserve the prototype's own state machine and methods in one place.

The injected host in `nautilus_my_computer/column_view.py` now keeps the same core methods as the standalone repro:

- `_poll_viewport_size`
- `_make_column`
- `_make_preview_column`
- `_on_add_column_clicked`
- `_on_trim_after_column_clicked`
- `_rebuild_chain`
- `_sync_column_controls`
- `_detach_paned_children`
- `_make_paned_chain`
- `_on_paned_position_changed`
- `_sync_root_width`
- `_schedule_scroll_to_end`
- `_on_hadjustment_changed`
- `_on_hadjustment_value_changed`
- `_arm_scroll_ceiling_release`
- `_release_scroll_ceiling`
- `_apply_pending_scroll`

The remaining fixes came from tightening the geometry and scroll rules around `_sync_root_width()` and `_on_paned_position_changed()`.

#### Scroll drift and clamp behavior

The resize path snapshots the horizontal adjustment and clamps rightward drift during resize bursts. An important refinement was making extra drag beyond a clamped width a real no-op:

```python
old_width = self.widths[index]
width = max(COLUMN_MIN_WIDTH, min(COLUMN_MAX_WIDTH, paned.get_position()))

if width == old_width:
    return
```

That stopped repeated min-width drag motion from re-arming the scroll ceiling and retriggering width sync when no real width change had occurred.

#### Preview fill and visible span

The final effective fix was teaching `_sync_root_width()` to account for the actual visible right edge, not only the viewport width:

```python
visible_right_edge = viewport_width + self.scroller.get_hadjustment().get_value()
canvas_width = max(total_width, viewport_width, visible_right_edge)
```

That matters when the user is already scrolled to the right. Before this change, the preview could drop back to `PREVIEW_WIDTH` even though there was still visible empty area on screen, because the width calculation only knew about the viewport's size, not where the viewport currently ended in content coordinates.

The paned chain itself is also requested at the effective canvas width, so the preview absorbs temporary extra width directly instead of leaving spare space outside the chain.

#### Nautilus integration

`nautilus_my_computer/main.py` now detects `/home/yann/Music` with `_window_is_at_column_trigger()` and switches to `VIEW_COLUMN` from `_on_title_changed()`.

This keeps the integration thin: the overlay/view switching remains in `main.py`, while the resize behavior stays in the prototype-backed target module.

### Files affected

- `nautilus_my_computer/column_view.py`
- `nautilus_my_computer/column_view_test.py`
- `nautilus_my_computer/main.py`
- `nautilus_my_computer/common.py`
- `nautilus_my_computer/widgets.py`
- `tmp/column_resize_paned_repro.py`

### Related commits

- `fix: stabilize prototype column resize behavior`

### Notes

#### Validation performed

The following checks were used repeatedly during the session:

```SHELL
PYTHONDONTWRITEBYTECODE=1 python -m py_compile \
  tmp/column_resize_paned_repro.py \
  nautilus_my_computer/column_view_test.py \
  nautilus_my_computer/column_view.py \
  nautilus_my_computer/main.py
```

```SHELL
cmp -s tmp/column_resize_paned_repro.py nautilus_my_computer/column_view_test.py && echo identical
```

```SHELL
PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
from nautilus_my_computer.main import MyComputerExtension
print(MyComputerExtension.__name__)
PY
```

```SHELL
./install.sh
```

The `cmp` check stayed clean for `column_view_test.py`, which confirms the exact-copy file still matches the standalone prototype byte-for-byte.

#### Final state

At the end of the session, the user confirmed that the latest resize behavior worked. The decisive correction was the visible-right-edge aware preview fill rule in `_sync_root_width()`, with the min/max no-op and chain-width handling acting as supporting fixes.

#### Constraints preserved

- The older custom Miller-column implementation was not restored.
- The standalone repro remains the authoritative behavior reference.
- The injected Nautilus version keeps only a thin wrapper around the prototype-backed host.

### Examples

Open the repro-backed view in Nautilus by navigating to:

```SHELL
/home/yann/Music
```

Useful local comparison commands:

```SHELL
diff -u tmp/column_resize_paned_repro.py nautilus_my_computer/column_view.py | sed -n '1,260p'
```

```SHELL
diff -u tmp/column_resize_paned_repro.py nautilus_my_computer/column_view_test.py
```