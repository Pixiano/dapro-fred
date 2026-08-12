# Pins two bits of real logic added for the pill's third (type) button:
# the width-recalculation math in render.button_centres()/PILL_W, and
# window.PillWindow._hit_button's per-circle dispatch across three
# buttons instead of two. Everything else in this feature is Win32 API
# glue (window/control creation, message routing) with no branching
# worth a unit test — see the module docstrings for why that part can
# only be confirmed by a live human check.

from ui.pill import render as R
from ui.pill.window import PillWindow


def test_three_buttons_ordered_left_to_right_and_non_overlapping():
    (lx, _), (rx, _), (tx, _) = R.button_centres()
    assert lx < rx < tx
    assert (rx - lx) >= R.BTN_D  # cancel/accept don't overlap
    assert (tx - rx) >= R.BTN_D  # accept/type don't overlap


def test_pill_and_buttons_fit_inside_the_canvas():
    assert R.PILL_X >= 0
    assert R.PILL_X + R.PILL_W <= R.CANVAS_W
    (_, _), (_, _), (tx, _) = R.button_centres()
    assert tx + R.BTN_D / 2.0 <= R.PILL_X + R.PILL_W


def _bare_window():
    return PillWindow.__new__(PillWindow)


def test_hit_button_identifies_each_of_the_three_buttons():
    win = _bare_window()
    (lx, ly), (rx, ry), (tx, ty) = R.button_centres()

    assert win._hit_button(lx, ly) == "cancel"
    assert win._hit_button(rx, ry) == "accept"
    assert win._hit_button(tx, ty) == "type"


def test_hit_button_misses_between_buttons():
    win = _bare_window()
    (lx, ly), (rx, ry) = R.button_centres()[0], R.button_centres()[1]
    midpoint_x = (lx + rx) / 2.0
    assert win._hit_button(midpoint_x, ly) is None
