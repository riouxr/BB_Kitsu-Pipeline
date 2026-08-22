"""Frame range, frame rate and resolution, as Kitsu reports them.

Kitsu is loose about both, so every DCC would otherwise reinvent the same
defensive parsing:

* A shot's ``frame_in`` and ``frame_out`` columns exist but are usually empty.
  The real values live in the shot's custom ``data`` dict, which is free-form
  and arrives as strings as often as integers.
* ``nb_frames`` is a separate column and can disagree with the pair.
* A project's ``fps`` is a *string* - ``"24"``, sometimes ``"23.976"``.
* Its ``resolution`` is a string too - ``"3840x2160"``.

Everything here is pure and takes plain dicts, so the same answers reach
Blender, Nuke and Houdini.
"""

from fractions import Fraction

# Where a frame range hides, best first. Kitsu's own columns are checked
# before the custom data, so a studio that starts filling them in wins.
IN_KEYS = ("frame_in", "frameIn", "start_frame", "frame_start")
OUT_KEYS = ("frame_out", "frameOut", "end_frame", "frame_end")


def _number(value):
    """A value Kitsu might have stored as a string, an int, or nothing."""
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _look_up(entity, keys):
    for source in (entity, entity.get("data") or {}):
        if not isinstance(source, dict):
            continue
        for key in keys:
            found = _number(source.get(key))
            if found is not None:
                return found
    return None


def frame_range(entity):
    """``(start, end)`` for a shot, or None when Kitsu does not say.

    Falls back to ``nb_frames`` counted from frame 1 when only the length is
    known, because a length with no offset is still better than opening a
    shot on Blender's default 1-250.
    """
    if not entity:
        return None

    start = _look_up(entity, IN_KEYS)
    end = _look_up(entity, OUT_KEYS)

    if start is not None and end is not None:
        return (start, end) if end >= start else (end, start)

    count = _number(entity.get("nb_frames"))
    if count and count > 0:
        if start is not None:
            return start, start + count - 1
        return 1, count

    return None


def frame_count(entity):
    span = frame_range(entity)
    return (span[1] - span[0] + 1) if span else None


def fps(project, entity=None):
    """The frame rate as a float, preferring anything set on the entity.

    Kitsu stores it as a string on the project. A shot may carry its own in
    custom data, which wins - a project-wide rate is a default, not a law.
    """
    for source in (entity or {}, (entity or {}).get("data") or {},
                   project or {}, (project or {}).get("data") or {}):
        if not isinstance(source, dict):
            continue
        value = source.get("fps")
        if value in (None, ""):
            continue
        try:
            rate = float(str(value).strip())
        except (TypeError, ValueError):
            continue
        if rate > 0:
            return rate
    return None


def fps_to_rational(rate, tolerance=0.0005):
    """A frame rate as the ``(fps, fps_base)`` pair Blender stores.

    Blender holds the rate as an integer over a base, so 24 is ``(24, 1.0)``
    and 23.976 is ``(24, 1.001)``. Rounding 23.976 to 24 would drift a frame
    every forty seconds, which is exactly the kind of error that only shows up
    once a cut is conformed.
    """
    if not rate or rate <= 0:
        return None

    if abs(rate - round(rate)) < tolerance:
        return int(round(rate)), 1.0

    # The broadcast rates are all n/1.001; recognise them exactly rather than
    # letting a float approximation invent 24000/1001 as something else.
    for whole in (24, 30, 60, 120):
        if abs(rate - (whole / 1.001)) < tolerance:
            return whole, 1.001

    fraction = Fraction(rate).limit_denominator(1000)
    return fraction.numerator, float(fraction.denominator)


def describe(rate):
    """A frame rate written the way people say it."""
    if not rate:
        return "unknown"
    if abs(rate - round(rate)) < 0.0005:
        return "%d fps" % round(rate)
    return "%.3f fps" % rate


def resolution(project, entity=None):
    """``(width, height)`` for the show, or None when Kitsu does not say.

    Stored as a string like ``"3840x2160"``. A shot may carry its own in
    custom data - a stereo plate or a title card can differ from the show -
    and that wins over the project.
    """
    for source in (entity or {}, (entity or {}).get("data") or {},
                   project or {}, (project or {}).get("data") or {}):
        if not isinstance(source, dict):
            continue
        value = source.get("resolution")
        if not value:
            continue

        text = str(value).strip().lower()
        for separator in ("×", "*", ":"):
            text = text.replace(separator, "x")
        parts = text.split("x")
        if len(parts) != 2:
            continue
        width, height = _number(parts[0]), _number(parts[1])
        if width and height and width > 0 and height > 0:
            return width, height
    return None
