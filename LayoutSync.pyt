# -*- coding: utf-8 -*-
"""ArcGIS Pro 3.2+ | Layout Standards Manager POC v0.3.0.

Master-first CIM synchronization of existing STD_ layout elements. The master
.pagx controls the styling and geometry. Analyst project maps, cameras,
map-frame links and legend layer identities are protected. No Git required.

IMPORTANT: This is a test prototype, not validated in an ArcGIS Pro runtime.
Test on disposable copies of your actual .aprx projects before deployment.
"""

import datetime
import hashlib
import json
import math
import os
from pathlib import Path

import arcpy

VERSION = "0.3.0"
CIM_VERSION = "V3"
SUPPORTED_TYPES = {
    "TEXT_ELEMENT", "LEGEND_ELEMENT", "MAPFRAME_ELEMENT",
    "MAPSURROUND_ELEMENT", "GRAPHIC_ELEMENT", "PICTURE_ELEMENT",
}

# These are identity / local state, not visual formatting. We obtain the FULL
# master element definition and put these fields back from the analyst's element.
# Everything else -- including nested colors, marks, text symbols, borders,
# neatlines, graphic frames, etc. -- comes from the master CIM definition.
BASE_PROTECTED = (
    "name", "customProperties", "expanded", "iD", "elementID",
)
MAPFRAME_PROTECTED = (
    "uRI", "view", "autoCamera", "grids", "extentIndicators",
    "layerVisibilityOverrides", "overrideLayerVisibility",
    "mapGridsExpanded", "extentIndicatorsExpanded",
)
MAPSURROUND_PROTECTED = ("mapFrame",)
LEGEND_PROTECTED = ("mapFrame", "excludedLayers")

# Legend items come from a fresh compatible master definition, with just the
# local item's identity, linked layer and per-layer visibility/placement
# restored. This is intentionally a small list of CONTENT exceptions rather
# than an ever-growing list of the master's style properties.
LEGEND_ITEM_PROTECTED = (
    "name", "layer", "isVisible", "autoVisibility", "groupFilter",
    "manualColumn", "newColumn", "useMapSeriesShape",
)

# Names must be unique across all standardized layout elements.


def _log(message, level="message"):
    if level == "error":
        arcpy.AddError(message)
    elif level == "warning":
        arcpy.AddWarning(message)
    else:
        arcpy.AddMessage(message)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cim_name(cim):
    return type(cim).__name__


def _same_class(a, b):
    return _cim_name(a) == _cim_name(b)


def _restore_fields(candidate, original, fields, required=()):
    """Restore protected fields from a detached analyst CIM definition.

    Strict preflight for required references: fail if the CIM subclass doesn't
    provide them rather than risk making an invalid map/legend link.
    """
    for field in required:
        if not hasattr(candidate, field) or not hasattr(original, field):
            raise ValueError("Required analyst CIM reference {!r} absent on {}".format(
                field, _cim_name(original)))
    restored = []
    for field in fields:
        if hasattr(candidate, field) and hasattr(original, field):
            setattr(candidate, field, getattr(original, field))
            restored.append(field)
    return restored


def _legend_item_key(item):
    return (str(getattr(item, "name", "")), _cim_name(item))


def _merge_legend_items(master_element, master_cim, target_cim):
    """Build independent master-styled items while retaining target layers.

    A same-name/same-type master item is used when possible; otherwise a
    same-type master item serves as the style exemplar. The master default item
    is the last fallback. A missing compatible prototype is a *hard error*.
    """
    master_items = list(getattr(master_cim, "items", None) or [])
    target_items = list(getattr(target_cim, "items", None) or [])
    master_index = {}
    for index, item in enumerate(master_items):
        master_index.setdefault(_legend_item_key(item), []).append(index)

    default_item = getattr(master_cim, "defaultLegendItem", None)
    replacement_items = []
    counts = {"name": 0, "same_type": 0, "default": 0}

    for analyst_item in target_items:
        key = _legend_item_key(analyst_item)
        match_index = None
        mode = None
        if len(master_index.get(key, [])) == 1:
            match_index = master_index[key][0]
            mode = "name"
        if match_index is None:
            match_index = next((i for i, item in enumerate(master_items)
                                if _same_class(item, analyst_item)), None)
            if match_index is not None:
                mode = "same_type"
        if match_index is None and default_item is not None and _same_class(default_item, analyst_item):
            mode = "default"
        if mode is None:
            raise ValueError(
                "No compatible master legend-item style for analyst item {!r} ({}). "
                "Add a matching item type to the master legend or its default item.".format(
                    key[0], key[1]))
        # getDefinition returns a new CIM object graph. Retrieve a fresh item
        # for each analyst layer even when several use the same style exemplar.
        # Python deepcopy cannot copy ArcPy geometry objects in CIM definitions.
        fresh_master = master_element.getDefinition(CIM_VERSION)
        if mode == "default":
            styled_item = fresh_master.defaultLegendItem
        else:
            styled_item = fresh_master.items[match_index]
        _restore_fields(styled_item, analyst_item, LEGEND_ITEM_PROTECTED, required=("layer",))
        replacement_items.append(styled_item)
        counts[mode] += 1

    return replacement_items, counts


def _master_first_element(master_element, analyst_element):
    """Build a new CIM without editing the live ArcGIS Pro project.

    Works with the entire CIM class (and nested style objects), not a list of
    approved symbol property names. Element classes must be identical.
    """
    master_cim = master_element.getDefinition(CIM_VERSION)
    analyst_cim = analyst_element.getDefinition(CIM_VERSION)
    if not _same_class(master_cim, analyst_cim):
        raise ValueError("CIM type mismatch for {}: {} versus {}".format(
            master_element.name, _cim_name(master_cim), _cim_name(analyst_cim)))

    # ArcPy returns a detached definition; editing it does not affect the live
    # element until setDefinition. It can contain non-deepcopyable ArcPy points.
    result = master_cim
    protected = _restore_fields(result, analyst_cim, BASE_PROTECTED, required=("name",))
    kind = master_element.type
    extra = {}

    if kind == "MAPFRAME_ELEMENT":
        protected += _restore_fields(
            result, analyst_cim, MAPFRAME_PROTECTED, required=("uRI", "view"))
    elif kind == "MAPSURROUND_ELEMENT":
        protected += _restore_fields(
            result, analyst_cim, MAPSURROUND_PROTECTED, required=("mapFrame",))
    elif kind == "LEGEND_ELEMENT":
        protected += _restore_fields(
            result, analyst_cim, LEGEND_PROTECTED, required=("mapFrame", "items"))
        items, counts = _merge_legend_items(master_element, master_cim, analyst_cim)
        result.items = items
        extra["legend_items"] = len(items)
        extra["legend_style_matches"] = counts

        # A master default item must not bring a master layer reference into
        # the analyst project. Keep the target's default item's identity fields
        # when the CIM classes are compatible.
        new_default = getattr(result, "defaultLegendItem", None)
        old_default = getattr(analyst_cim, "defaultLegendItem", None)
        if new_default is not None and hasattr(new_default, "layer"):
            if old_default is not None and _same_class(new_default, old_default):
                _restore_fields(new_default, old_default, LEGEND_ITEM_PROTECTED)
            else:
                # No matching local default: do not keep a source-project layer.
                new_default.layer = None

    # Text/graphics/pictures: master owns the entire CIM appearance/geometry.
    # For map frames and surrounds, only the fields above are excluded.
    return result, protected, extra


def _exact_layout(aprx, name):
    layouts = [lyt for lyt in aprx.listLayouts() if lyt.name == name]
    if len(layouts) != 1:
        raise ValueError("Expected exactly one layout named {!r}; found {}".format(
            name, len(layouts)))
    return layouts[0]


def _standard_elements(layout, prefix):
    elements = {}
    for element in layout.listElements():
        if not element.name.startswith(prefix):
            continue
        if element.name in elements:
            raise ValueError("Duplicate STD_ element name: {}".format(element.name))
        if element.type not in SUPPORTED_TYPES or getattr(element, "isGroup", False):
            raise ValueError("Unsupported standardized element {} ({})".format(
                element.name, element.type))
        if getattr(element, "parentGroupElement", None) is not None:
            raise ValueError("Standardized element {} is grouped; ungroup it for v0.3".format(
                element.name))
        elements[element.name] = element
    return elements


def _number_equal(a, b):
    try:
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-7)
    except (TypeError, ValueError):
        return a == b


def _validate(master_layout, analyst_layout, prefix):
    if str(master_layout.pageUnits).upper() != str(analyst_layout.pageUnits).upper():
        raise ValueError("Page units differ between master and analyst")
    for name in ("pageWidth", "pageHeight"):
        if not _number_equal(getattr(master_layout, name), getattr(analyst_layout, name)):
            raise ValueError("Page {} differs: change page size outside this prototype".format(name))
    source = _standard_elements(master_layout, prefix)
    target = _standard_elements(analyst_layout, prefix)
    if not source:
        raise ValueError("Master contains no elements with prefix {!r}".format(prefix))
    missing = sorted(set(source) - set(target))
    extra = sorted(set(target) - set(source))
    if missing or extra:
        raise ValueError("STD_ element mismatch. Missing in analyst: {}; extra in analyst: {}".format(
            missing, extra))
    for name, master_el in source.items():
        if master_el.type != target[name].type:
            raise ValueError("Element type mismatch for {}".format(name))
    return source, target


def _simple_diffs(master_element, analyst_element):
    """Helpful human-readable preview; NOT an exhaustive nested CIM diff."""
    fields = ("elementWidth", "elementHeight", "elementPositionX",
              "elementPositionY", "elementRotation", "text", "textSize",
              "fontStyleName")
    changes = []
    for field in fields:
        if hasattr(master_element, field) and hasattr(analyst_element, field):
            source_value = getattr(master_element, field)
            target_value = getattr(analyst_element, field)
            if not _number_equal(source_value, target_value):
                changes.append("{}: {!r} -> {!r}".format(field, target_value, source_value))
    src_cim = master_element.getDefinition(CIM_VERSION)
    tgt_cim = analyst_element.getDefinition(CIM_VERSION)
    for field in ("divisionMarkHeight", "subdivisionMarkHeight",
                  "midpointMarkHeight", "barHeight", "barWidth", "columns"):
        if hasattr(src_cim, field) and hasattr(tgt_cim, field):
            before, after = getattr(tgt_cim, field), getattr(src_cim, field)
            if not _number_equal(before, after):
                changes.append("CIM.{}: {!r} -> {!r}".format(field, before, after))
    return changes


def _camera_snapshot(frame):
    """Capture useful camera scalars; inaccessible values omitted."""
    camera = getattr(frame, "camera", None)
    if camera is None:
        return {}
    names = ("X", "Y", "Z", "scale", "heading", "pitch", "roll", "mode")
    out = {}
    for name in names:
        if hasattr(camera, name):
            out[name] = getattr(camera, name)
    return out


def _camera_value_equal(before, after):
    """Treat two undefined camera ordinates as unchanged, but detect real changes."""
    if isinstance(before, float) and isinstance(after, float):
        if math.isnan(before) and math.isnan(after):
            return True
    return _number_equal(before, after)


def _restore_camera(frame, snapshot):
    """Restore the analyst's camera after frame geometry changes."""
    if not snapshot:
        return
    camera = getattr(frame, "camera", None)
    if camera is None:
        raise RuntimeError("Camera is unavailable for {}".format(frame.name))
    mode = snapshot.get("mode")
    if mode is not None and getattr(camera, "mode", None) != mode:
        raise RuntimeError("Camera mode changed in {}".format(frame.name))
    if mode in (None, "MAP"):
        fields = ("X", "Y", "heading", "scale")
    elif mode in ("GLOBAL", "LOCAL"):
        # 3D scale is an estimate, not the camera position. Restore the actual
        # position and orientation instead of setting that derived value.
        fields = ("X", "Y", "Z", "heading", "pitch", "roll")
    else:
        raise RuntimeError("Unsupported camera mode {!r} in {}".format(mode, frame.name))
    for field in fields:
        if field not in snapshot:
            continue
        value = snapshot[field]
        # ArcPy returns NaN for ordinates that do not apply to a camera mode.
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if not hasattr(camera, field):
            raise RuntimeError("Camera {} is unavailable for {}".format(field, frame.name))
        if not _camera_value_equal(value, getattr(camera, field)):
            setattr(camera, field, value)


def _frame_signature(frame):
    map_obj = getattr(frame, "map", None)
    linked_name = getattr(map_obj, "name", None)
    layers = []
    if map_obj is not None:
        for lyr in map_obj.listLayers():
            layers.append((getattr(lyr, "URI", None),
                           getattr(lyr, "longName", getattr(lyr, "name", ""))))
    return {"map": linked_name, "layers": layers,
            "camera": _camera_snapshot(frame)}


def _legend_refs(cim):
    """Stable linked legend identifiers, independent of CIM object identity.

    We preserve additional per-item data (e.g. groupFilter) in the candidate
    but verify the stable scalar identity/visibility fields here. Some ArcPy
    CIM group-filter objects do not implement value equality.
    """
    identity_fields = (
        "name", "layer", "isVisible", "autoVisibility",
        "manualColumn", "newColumn", "useMapSeriesShape",
    )
    return {
        "mapFrame": getattr(cim, "mapFrame", None),
        "excludedLayers": tuple(getattr(cim, "excludedLayers", None) or []),
        "items": [tuple(getattr(item, n, None) for n in identity_fields)
                  for item in (getattr(cim, "items", None) or [])],
    }


def _protected_reference_snapshot(elements):
    result = {}
    for name, element in elements.items():
        kind = element.type
        cim = element.getDefinition(CIM_VERSION)
        if kind == "MAPFRAME_ELEMENT":
            result[name] = ("frame", getattr(cim, "uRI", None), _frame_signature(element))
        elif kind == "LEGEND_ELEMENT":
            result[name] = ("legend", _legend_refs(cim))
        elif kind == "MAPSURROUND_ELEMENT":
            result[name] = ("surround", getattr(cim, "mapFrame", None))
    return result


def _check_protected_state(before, after):
    """Post-setDefinition safeguard; abort before save on broken references."""
    if set(before) != set(after):
        raise RuntimeError("Protected element names changed unexpectedly")
    for name in before:
        lhs, rhs = before[name], after[name]
        # Direct comparison of CIM classes may use object identity. For map
        # surrounds, associations are typically a URI string. Compare strings.
        if lhs[0] == "surround":
            if str(lhs[1]) != str(rhs[1]):
                raise RuntimeError("Map surround reference changed: {}".format(name))
        elif lhs[0] == "frame":
            if lhs[1] != rhs[1]:
                raise RuntimeError("Linked map URI changed: {}".format(name))
            old_sig, new_sig = lhs[2], rhs[2]
            if old_sig["map"] != new_sig["map"] or old_sig["layers"] != new_sig["layers"]:
                raise RuntimeError("Map/layer identity changed: {}".format(name))
            for attr, value in old_sig["camera"].items():
                other = new_sig["camera"].get(attr)
                if not _camera_value_equal(value, other):
                    raise RuntimeError("Camera {} changed in {}: {!r} -> {!r}. "
                                       "Check map-frame resizing.".format(attr, name, value, other))
        elif lhs[0] == "legend":
            # Legend mapFrame is an associated URI/path. Items are scalars.
            x, y = lhs[1], rhs[1]
            if (str(x["mapFrame"]) != str(y["mapFrame"]) or
                    x["excludedLayers"] != y["excludedLayers"] or
                    x["items"] != y["items"]):
                raise RuntimeError("Legend map-frame or per-layer content changed: {}".format(name))


def _manifest(master_path):
    result = {"version": "unlabelled"}
    file_path = os.path.join(os.path.dirname(master_path), "version.json")
    if os.path.isfile(file_path):
        with open(file_path, "r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("version.json must be a JSON object")
        result.update(data)
    result["sha256"] = _sha256(master_path)
    return result


def _validate_picture_source(master_element):
    if master_element.type != "PICTURE_ELEMENT":
        return
    src = getattr(master_element, "sourceImage", None)
    if src and not os.path.isfile(src):
        raise ValueError("Master picture {} sourceImage not accessible: {}. "
                         "Use a shared image file and re-export the PAGX.".format(
                             master_element.name, src))


def sync_layout(master_path, layout_name, prefix="STD_", apply=False,
                backup_folder=None):
    """Preview or apply a complete master-style update to CURRENT layout."""
    if not master_path or not os.path.isfile(master_path) or not master_path.lower().endswith(".pagx"):
        raise ValueError("Master layout must be an existing .pagx file: {}".format(master_path))
    if not prefix:
        raise ValueError("Standard element prefix cannot be empty")
    project = arcpy.mp.ArcGISProject("CURRENT")
    if project.isReadOnly:
        raise RuntimeError("Current ArcGIS Pro project is read-only")
    project_path = project.filePath
    if not project_path or not os.path.isfile(project_path) or not project_path.lower().endswith(".aprx"):
        raise RuntimeError("Save the editable analyst project as an .aprx first")
    master_layout = arcpy.mp.ConvertLayoutFileToLayout(master_path)
    analyst_layout = _exact_layout(project, layout_name)
    master, analyst = _validate(master_layout, analyst_layout, prefix)
    version = _manifest(master_path)
    _log("Layout Standards Manager v{} | {}".format(VERSION, "APPLY" if apply else "PREVIEW"))
    _log("Analyst project: {}".format(project_path))
    _log("Master PAGX: {}".format(master_path))
    _log("Template version: {} | SHA256: {}".format(
        version.get("version", "unlabelled"), version["sha256"][:16]))

    # Preflight ALL candidates first. If any element is incompatible, no
    # ArcGIS project element has yet been edited.
    plans = {}
    for name in sorted(master):
        _validate_picture_source(master[name])
        proposed, protected, info = _master_first_element(master[name], analyst[name])
        plans[name] = {"cim": proposed, "protected": protected, "info": info}
        _log("  {} [{} -> {}] | retained {} | master CIM style/geometry".format(
            name, master[name].type, _cim_name(proposed), ", ".join(protected)))
        if info.get("legend_items") is not None:
            _log("    Legend: {} analyst layer items retained; matched styles {}".format(
                info["legend_items"], info["legend_style_matches"]))
        for difference in _simple_diffs(master[name], analyst[name])[:10]:
            _log("    " + difference)
    if not apply:
        _log("PREVIEW: no project modified. Nested symbol/color changes are copied "
             "but not comprehensively listed in preview.")
        return {"mode": "preview", "elements": len(plans), "release": version}

    backup_dir = Path(backup_folder) if backup_folder else Path(project_path).parent / "LayoutSync_Backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if backup_dir.resolve() == Path(project_path).parent.resolve():
        _log("Backups will be stored alongside the original; choose a separate "
             "backup location if the original is on a shared work area.", "warning")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / (Path(project_path).stem + "_before_layout_sync_" + stamp + ".aprx")
    project.saveACopy(str(backup_path))
    if not backup_path.is_file():
        raise RuntimeError("Backup creation failed: " + str(backup_path))
    _log("Backup created: {}".format(backup_path))

    before_refs = _protected_reference_snapshot(analyst)
    # Independent getDefinition calls capture detached rollback snapshots without
    # invoking deepcopy on native ArcPy geometry embedded in the CIM.
    previous_cims = {name: analyst[name].getDefinition(CIM_VERSION)
                     for name in plans}
    applied = []
    try:
        for name in sorted(plans):
            # Exact full CIM from master, with selected analyst-owned values.
            # This is NOT replacing the layout or changing the layers/maps.
            applied.append(name)  # include the element if setDefinition raises midway
            analyst[name].setDefinition(plans[name]["cim"])
            _log("    Applied master CIM: {}".format(name))

        # Resizing a map frame can recalculate its camera even though its CIM
        # view was retained. Restore the analyst's camera after all frame edits.
        for name, state in before_refs.items():
            if state[0] == "frame":
                _restore_camera(analyst[name], state[2]["camera"])

        # Fail closed before saving if a protected association/legend item/map
        # or map-frame camera unexpectedly changed in ArcGIS Pro.
        _check_protected_state(before_refs, _protected_reference_snapshot(analyst))
        project.save()
    except Exception:
        _log("SYNC FAILED before completing save; attempting in-memory rollback.", "error")
        for name in reversed(applied):
            try:
                analyst[name].setDefinition(previous_cims[name])
            except Exception as exc:
                _log("Rollback of {} failed: {}".format(name, exc), "error")
        # Restoring the previous frame dimensions can itself change the camera.
        for name in applied:
            state = before_refs.get(name)
            if state is not None and state[0] == "frame":
                try:
                    _restore_camera(analyst[name], state[2]["camera"])
                except Exception as exc:
                    _log("Camera rollback of {} failed: {}".format(name, exc), "error")
        _log("Close the project WITHOUT saving if changes remain. "
             "Pre-sync backup: {}".format(backup_path), "error")
        raise

    record = {
        "aprx": project_path,
        "layout": layout_name,
        "template_path": master_path,
        "version": version.get("version", "unlabelled"),
        "master_sha256": version["sha256"],
        "element_count": len(plans),
        "backup_aprx": str(backup_path),
        "tool_version": VERSION,
        "applied_at": datetime.datetime.now().astimezone().isoformat(),
    }
    try:
        with (backup_dir / (Path(project_path).stem + "_layout_sync_history.jsonl")).open(
                "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        _log("Project saved, but could not write sync history: {}".format(exc), "warning")
    _log("SUCCESS: updated {} existing standardized elements in place.".format(len(plans)))
    _log("Check printed map/PDF output for legend overflow, font rendering, "
         "scale-bar formatting and map-frame extent.")
    return record


class Toolbox(object):
    def __init__(self):
        self.label = "Layout Standards Manager (v0.3 POC)"
        self.alias = "LayoutSyncPOC"
        self.tools = [SynchronizeLayout]


class SynchronizeLayout(object):
    def __init__(self):
        self.label = "Preview or Synchronize Layout from Master PAGX"
        self.description = (
            "Master-first CIM synchronization. Existing STD_ element appearance "
            "is overwritten; analyst map/layer/camera references are protected."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        p0 = arcpy.Parameter(
            displayName="Master layout (.pagx) — shared folder", name="master_pagx",
            datatype="DEFile", parameterType="Required", direction="Input")
        p0.filter.list = ["pagx"]
        p1 = arcpy.Parameter(
            displayName="Existing analyst layout name (exact match)", name="layout_name",
            datatype="GPString", parameterType="Required", direction="Input")
        p1.value = "Standard_Layout"
        p2 = arcpy.Parameter(
            displayName="Standardized element name prefix", name="prefix",
            datatype="GPString", parameterType="Required", direction="Input")
        p2.value = "STD_"
        p3 = arcpy.Parameter(
            displayName="Mode", name="mode", datatype="GPString",
            parameterType="Required", direction="Input")
        p3.filter.type = "ValueList"
        p3.filter.list = ["Preview only", "Apply and save (creates backup)"]
        p3.value = "Preview only"
        p4 = arcpy.Parameter(
            displayName="Backup folder (optional)", name="backup_folder",
            datatype="DEFolder", parameterType="Optional", direction="Input")
        return [p0, p1, p2, p3, p4]

    def execute(self, parameters, messages):
        master = parameters[0].valueAsText
        layout_name = parameters[1].valueAsText
        prefix = parameters[2].valueAsText
        apply = parameters[3].valueAsText == "Apply and save (creates backup)"
        backup_folder = parameters[4].valueAsText or None
        sync_layout(master, layout_name, prefix=prefix, apply=apply,
                    backup_folder=backup_folder)
