# -*- coding: utf-8 -*-
"""ArcGIS Pro | Cartographic Standards Manager POC v0.4.3.

Master-first CIM synchronization of STD_ layout elements (v0.3.1 engine),
plus approved layer symbology and labeling from .lyrx files and a Project Lead
promote/publish workflow. Analyst feature data connections are preserved.
No Git required. Layer pasteProperties requires ArcGIS Pro 3.4+.

IMPORTANT: This is a test prototype, not validated in an ArcGIS Pro runtime.
Test on disposable copies of your actual .aprx projects before deployment.
"""

import copy
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import uuid
from pathlib import Path

import arcpy

VERSION = "0.4.3"
STANDARD_PREFIX = "STD_"
REGISTRY_FILE = "layer_standards.json"
APPROVED_DIR = "Approved_Layers"
LAYER_PROPERTIES = ["SYMBOLOGY", "LABELING"]
CIM_VERSION = "V3"
SUPPORTED_TYPES = {
    "TEXT_ELEMENT", "LEGEND_ELEMENT", "MAPFRAME_ELEMENT",
    "MAPSURROUND_ELEMENT", "GRAPHIC_ELEMENT", "PICTURE_ELEMENT",
}

# These are identity / local state, not visual formatting. We use the FULL
# master element definition and put only these fields back from the analyst's element.
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

# Legend items are cloned from a compatible master item, with just the
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
    """Restore analyst-owned fields without deep-copying ArcPy CIM objects.

    ArcGIS Pro may place geoprocessing Point objects inside CIM structures.
    These cannot be passed to copy.deepcopy. The caller obtains independent
    CIM definition snapshots via getDefinition("V3"); this function assigns
    analyst-owned field values without recursively cloning their internals.
    We do NOT mutate those retained objects after assignment.
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


def _merge_legend_items(master_cim, target_cim):
    """Clone compatible master legend item appearances; keep target layers.

    A same-name/same-type master item is used when possible; otherwise a
    same-type master item serves as the style exemplar. The master default item
    is the last fallback. A missing compatible prototype is a *hard error*.
    """
    master_items = list(getattr(master_cim, "items", None) or [])
    target_items = list(getattr(target_cim, "items", None) or [])
    master_index = {}
    for item in master_items:
        master_index.setdefault(_legend_item_key(item), []).append(item)

    default_item = getattr(master_cim, "defaultLegendItem", None)
    replacement_items = []
    counts = {"name": 0, "same_type": 0, "default": 0}

    for analyst_item in target_items:
        key = _legend_item_key(analyst_item)
        match = None
        mode = None
        if len(master_index.get(key, [])) == 1:
            match = master_index[key][0]
            mode = "name"
        if match is None:
            match = next((m for m in master_items if _same_class(m, analyst_item)), None)
            if match is not None:
                mode = "same_type"
        if match is None and default_item is not None and _same_class(default_item, analyst_item):
            match = default_item
            mode = "default"
        if match is None:
            raise ValueError(
                "No compatible master legend-item style for analyst item {!r} ({}). "
                "Add a matching item type to the master legend or its default item.".format(
                    key[0], key[1]))
        # A shallow clone creates a distinct item instance, preventing a
        # fallback master style from becoming the same item for 2+ analyst
        # layers. Nested style objects remain shared/read-only. deepcopy()
        # cannot handle some ArcGIS geoprocessing objects in the CIM.
        styled_item = copy.copy(match)
        if styled_item is match:
            raise RuntimeError("Could not create separate CIM legend item for {!r}".format(key[0]))
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

    # getDefinition returns an independent CIM definition to edit before
    # setDefinition. It is not necessary (or safe) to deepcopy it: real CIM
    # element graphs can contain un-deepcopyable geoprocessing Point objects.
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
        items, counts = _merge_legend_items(master_cim, analyst_cim)
        result.items = items
        extra["legend_items"] = len(items)
        extra["legend_style_matches"] = counts

        # A master default item must not bring a master layer reference into
        # the analyst project. Keep the target's default item's identity fields
        # when the CIM classes are compatible.
        new_default = getattr(result, "defaultLegendItem", None)
        old_default = getattr(analyst_cim, "defaultLegendItem", None)
        if new_default is not None and hasattr(new_default, "layer"):
            # Separate default item's identity from the original template.
            new_default = copy.copy(new_default)
            result.defaultLegendItem = new_default
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
            raise ValueError("Standardized element {} is grouped; ungroup it for v0.3.1".format(
                element.name))
        elements[element.name] = element
    return elements


def _number_equal(a, b):
    """Compare ArcPy camera scalars, including undefined 2D camera values.

    Camera.Z is normally NaN in a 2D ArcGIS Pro map frame. Two NaNs represent
    the same undefined state, while NaN versus a real value is a change.
    """
    try:
        af, bf = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    if math.isnan(af) or math.isnan(bf):
        return math.isnan(af) and math.isnan(bf)
    return math.isclose(af, bf, rel_tol=1e-9, abs_tol=1e-7)


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


def _frame_signature(frame):
    map_obj = getattr(frame, "map", None)
    linked_name = getattr(map_obj, "name", None)
    layers = []
    if map_obj is not None:
        for lyr in map_obj.listLayers():
            layers.append((getattr(lyr, "URI", None),
                           getattr(lyr, "longName", getattr(lyr, "name", ""))))
    # Track frame dimensions separately from the camera. The Project Lead
    # owns the dimensions; resizing can legitimately adjust the viewing scale.
    return {"map": linked_name, "layers": layers,
            "frame_size": (getattr(frame, "elementWidth", None),
                           getattr(frame, "elementHeight", None)),
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
            old_size = old_sig.get("frame_size", (None, None))
            new_size = new_sig.get("frame_size", (None, None))
            resized = any(not _number_equal(old, new)
                          for old, new in zip(old_size, new_size))
            if resized:
                _log("Map frame {} resized from {} to {} (master dimensions).".format(
                    name, old_size, new_size))
            for attr, value in old_sig["camera"].items():
                other = new_sig["camera"].get(attr)
                if _number_equal(value, other):
                    continue
                if resized and attr == "scale":
                    # Esri may change camera scale to accommodate a new frame
                    # aspect ratio / size. This is not a data-source change.
                    _log("Map frame {} scale adjusted on resize: {!r} -> {!r}. "
                         "Review visible extent before export.".format(
                             name, value, other), "warning")
                    continue
                raise RuntimeError(
                    "Camera {} changed unexpectedly in {}: {!r} -> {!r}. "
                    "Map-frame resizing is allowed; unintended camera-centre "
                    "or orientation changes are not. Inspect the layout or "
                    "map-frame constraints.".format(attr, name, value, other))
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


def sync_layout(master_path, layout_name=None, prefix=STANDARD_PREFIX, apply=False,
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
    if layout_name is None:
        layout_name = master_layout.name
        _log("Automatically matching analyst layout named {!r}".format(layout_name))
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
    # Rollback snapshots are fresh CIM definitions from the analyst elements.
    # Do not deepcopy: geometry/point objects in ArcPy are not deepcopyable.
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



# ---------------------------------------------------------------------------
# Approved feature-layer standards — independent of the v0.3.1 layout engine.
# ---------------------------------------------------------------------------

def _current_project():
    project = arcpy.mp.ArcGISProject("CURRENT")
    if project.isReadOnly:
        raise RuntimeError("The current ArcGIS Pro project is read-only")
    path = getattr(project, "filePath", "")
    if not path or not os.path.isfile(path) or not path.lower().endswith(".aprx"):
        raise RuntimeError("Save the current project as an editable .aprx first")
    return project


def _safe_slug(value):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    if not slug:
        raise ValueError("Invalid empty standard identifier")
    return slug


def _standard_name(layer_name):
    name = layer_name.strip()
    if not name:
        raise ValueError("A layer must have a nonempty name")
    return name if name.startswith(STANDARD_PREFIX) else STANDARD_PREFIX + name


def _unique_map(project, name):
    maps = [m for m in project.listMaps() if m.name == name]
    if len(maps) != 1:
        raise ValueError("Expected one map {!r}, found {}".format(name, len(maps)))
    return maps[0]


def _feature_layer(layer):
    return bool(getattr(layer, "isFeatureLayer", False) and
                not getattr(layer, "isGroupLayer", False))


def _layer_contents_path(layer):
    """ArcGIS Pro Contents path, including any parent group layers."""
    return str(getattr(layer, "longName", layer.name))


def _layer_by_long_name(map_obj, supplied_name):
    """Accept a full Contents path, or an unambiguous short feature-layer name.

    Map.listLayers() includes children of nested groups. A short layer name
    remains convenient when unique, while a path such as
    'Habitat\\Wildlife Habitat Areas' resolves duplicate names safely.
    """
    candidates = [lyr for lyr in map_obj.listLayers() if _feature_layer(lyr)]
    matches = [lyr for lyr in candidates if _layer_contents_path(lyr) == supplied_name]
    if not matches and "\\" not in supplied_name:
        matches = [lyr for lyr in candidates if lyr.name == supplied_name]
    if len(matches) != 1:
        available = [_layer_contents_path(lyr) for lyr in candidates]
        raise ValueError(
            "Could not uniquely identify feature layer {!r} in map {!r}: {} matches. "
            "Choose its full Contents path when grouped or duplicated. Available: {}".format(
                supplied_name, map_obj.name, len(matches), available))
    return matches[0]


def _dataset_basename(layer):
    """Use FC name, not the workspace path, so copied .gdb files can match."""
    path = str(getattr(layer, "dataSource", "") or "").replace("\\", "/").rstrip("/")
    if not path:
        raise ValueError("Layer {!r} has no readable dataset path".format(layer.name))
    basename = path.rsplit("/", 1)[-1]
    return basename.lower()


def _fields_for_layer(layer, described=None):
    """Read fields without passing a nested arcpy.mp.Layer to ListFields.

    arcpy.ListFields(layer) may resolve a grouped layer to a bare Contents
    display name and raise OSError('... does not exist'). Describe properties
    are preferred; dataset-path fallback works for ordinary file GDB/SDE FCs.
    """
    if described is None:
        described = arcpy.Describe(layer)
    for value in (described, getattr(described, "dataElement", None)):
        if value is not None:
            fields = getattr(value, "fields", None)
            if fields is not None:
                return list(fields)
    source = str(getattr(layer, "dataSource", "") or "")
    if not source:
        raise ValueError("Layer {!r} has no data source for field validation".format(
            _layer_contents_path(layer)))
    try:
        return list(arcpy.ListFields(source))
    except (OSError, RuntimeError) as exc:
        raise ValueError(
            "Could not inspect fields for {!r} from its data source {!r}: {}".format(
                _layer_contents_path(layer), source, exc)) from exc


def _layer_profile(layer):
    if not _feature_layer(layer):
        raise ValueError("Not a supported feature layer: {}".format(layer.name))
    if getattr(layer, "isBroken", False):
        raise ValueError("Layer {!r} has a broken data source".format(
            _layer_contents_path(layer)))
    desc = arcpy.Describe(layer)
    geometry = str(getattr(desc, "shapeType", "") or "")
    if not geometry:
        source = str(getattr(layer, "dataSource", "") or "")
        if source:
            geometry = str(getattr(arcpy.Describe(source), "shapeType", "") or "")
    if geometry.lower() not in ("point", "multipoint", "polyline", "polygon"):
        raise ValueError("Unsupported/unknown feature geometry for {!r}: {}".format(
            _layer_contents_path(layer), geometry))
    ignored = {"oid", "geometry", "globalid", "blob", "raster"}
    fields = sorted(
        [{"name": f.name, "type": f.type} for f in _fields_for_layer(layer, desc)
         if str(f.type).lower() not in ignored],
        key=lambda f: f["name"].lower())
    return {"geometry_type": geometry,
            "dataset_name": _dataset_basename(layer),
            "required_fields": fields}


def _validate_compatible(profile, layer):
    target = _layer_profile(layer)
    if profile["geometry_type"].lower() != target["geometry_type"].lower():
        raise ValueError("Geometry mismatch for {!r}: standard {}, analyst {}".format(
            layer.name, profile["geometry_type"], target["geometry_type"]))
    if profile["dataset_name"].lower() != target["dataset_name"].lower():
        raise ValueError("Dataset feature-class name mismatch for {!r}: {!r} vs {!r}. "
                         "Workspace locations may differ; feature-class names must match."
                         .format(layer.name, profile["dataset_name"], target["dataset_name"]))
    fields = {f["name"].lower(): f["type"].lower() for f in target["required_fields"]}
    missing = ["{} ({})".format(f["name"], f["type"])
               for f in profile["required_fields"]
               if fields.get(f["name"].lower()) != f["type"].lower()]
    if missing:
        raise ValueError("Analyst layer {!r} is missing/incompatible for required fields: {}".format(
            layer.name, ", ".join(missing)))
    return target


def _read_registry(folder, required=True):
    path = Path(folder) / REGISTRY_FILE
    if not path.exists():
        if required:
            raise FileNotFoundError("No {} at {}. Project Lead must promote a layer first.".format(
                REGISTRY_FILE, folder))
        return {"schema_version": 1, "standards": []}
    with path.open("r", encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("standards"), list):
        raise ValueError("Unsupported/invalid layer_standards.json; expected schema_version 1")
    seen = set()
    for entry in data["standards"]:
        required_keys = ("id", "map_name", "standard_layer_name", "source_layer_name",
                         "layer_file", "sha256", "geometry_type", "dataset_name", "required_fields")
        if not isinstance(entry, dict) or any(k not in entry for k in required_keys):
            raise ValueError("Incomplete layer-standard registry entry")
        if entry["id"] in seen:
            raise ValueError("Duplicate registry standard ID: {}".format(entry["id"]))
        seen.add(entry["id"])
        if not entry["standard_layer_name"].startswith(STANDARD_PREFIX):
            raise ValueError("Approved standard has no STD_ prefix: {}".format(entry["standard_layer_name"]))
        if (not isinstance(entry["required_fields"], list) or
                entry.get("properties", LAYER_PROPERTIES) != LAYER_PROPERTIES):
            raise ValueError("Invalid schema/properties in registry: {}".format(entry["id"]))
        _approved_file(folder, entry["layer_file"])
    return data


def _approved_file(folder, relative_file):
    path = Path(folder) / relative_file
    full = os.path.abspath(str(path))
    allowed = os.path.abspath(str(Path(folder) / APPROVED_DIR))
    if os.path.commonpath([full, allowed]) != allowed or not full.lower().endswith(".lyrx"):
        raise ValueError("Registry layer_file is not inside Approved_Layers: {!r}".format(relative_file))
    return Path(full)


def _write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


def _backup_project(project, backup_folder, tag):
    project_path = Path(project.filePath)
    directory = Path(backup_folder) if backup_folder else project_path.parent / "LayoutSync_Backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = "{}_before_{}_{}.aprx".format(project_path.stem, tag, stamp)
    path = directory / filename
    project.saveACopy(str(path))
    if not path.is_file():
        raise RuntimeError("ArcGIS project backup not created: {}".format(path))
    _log("Project backup: {}".format(path))
    return path


def _record_history(backup_path, tag, payload):
    log_file = Path(backup_path).with_name(Path(backup_path).stem + "_" + tag + "_history.json")
    try:
        _write_json_atomic(log_file, payload)
        _log("Audit record: {}".format(log_file))
    except OSError as exc:
        _log("Could not write audit record: {}".format(exc), "warning")


def _eligible_standard_targets(map_obj, entry):
    accepted_names = {entry["standard_layer_name"], entry["source_layer_name"]}
    all_layers = map_obj.listLayers()   # includes group descendants
    matches = [lyr for lyr in all_layers if
               getattr(lyr, "name", None) in accepted_names and _feature_layer(lyr)]
    if len(matches) > 1:
        # Exact group paths take precedence when the approved registry has
        # them. Older v0.4.1 registry entries lack these optional keys; if
        # several names are present, require unambiguous user intervention.
        paths = {entry.get("source_layer_long_name"),
                 entry.get("standard_layer_long_name")}
        paths.discard(None)
        exact = [lyr for lyr in matches if _layer_contents_path(lyr) in paths]
        if len(exact) == 1:
            return exact[0]
        raise ValueError("Ambiguous match in map {!r} for {}: {}. "
                         "Layer names must be unique, or group paths must "
                         "match the published standard.".format(
                             map_obj.name, entry["id"],
                             [_layer_contents_path(lyr) for lyr in matches]))
    if not matches:
        conflicting = [lyr for lyr in all_layers if
                       getattr(lyr, "name", None) in accepted_names and not _feature_layer(lyr)]
        if conflicting:
            raise ValueError("Matching name exists but is not a feature layer: {}".format(
                [_layer_contents_path(lyr) for lyr in conflicting]))
    return matches[0] if matches else None


def _read_approved_layer(folder, entry):
    path = _approved_file(folder, entry["layer_file"])
    if not path.is_file():
        raise FileNotFoundError("Approved layer file missing: {}".format(path))
    checksum = _sha256(path)
    if checksum != entry["sha256"]:
        raise ValueError("Approved layer .lyrx does not match published SHA256: {}. "
                         "Ask Project Lead to republish.".format(path))
    lyrx = arcpy.mp.LayerFile(str(path))
    layers = lyrx.listLayers()
    if len(layers) != 1 or not _feature_layer(layers[0]):
        raise ValueError("Approved .lyrx must contain exactly one feature layer: {}".format(path))
    return layers[0], path


def preview_or_sync_layers(folder, apply=False, backup_folder=None):
    """Validate/paste approved styles into matching existing map layers.

    The data source and feature contents are never intentionally replaced.
    Approved layer names are enforced on successfully updated target layers.
    Registry entries with no analyst match are reported and skipped.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError("Standards folder does not exist: {}".format(folder))
    registry = _read_registry(folder)
    project = _current_project()
    _log("Cartographic Standards Manager v{} | LAYER {}".format(
        VERSION, "APPLY" if apply else "PREVIEW"))
    _log("Analyst project: {}".format(project.filePath))
    _log("Standards folder: {}".format(folder))
    plans, skipped = [], []
    for entry in registry["standards"]:
        map_obj = _unique_map(project, entry["map_name"])
        target = _eligible_standard_targets(map_obj, entry)
        if target is None:
            _log("  SKIP {}: no matching existing layer".format(entry["id"]), "warning")
            skipped.append(entry["id"])
            continue
        _validate_compatible(entry, target)
        approved, path = _read_approved_layer(folder, entry)
        has_paste = callable(getattr(target, "pasteProperties", None))
        if apply and not has_paste:
            raise RuntimeError("ArcGIS Pro 3.4+ Layer.pasteProperties is required to apply styles "
                               "(missing on {!r}).".format(target.name))
        plans.append({"entry": entry, "target": target, "approved": approved,
                      "map": map_obj, "path": path})
        _log("  {} | map {!r} | {!r} -> {!r} | style: {} | approved {}".format(
            entry["id"], map_obj.name, _layer_contents_path(target), entry["standard_layer_name"],
            ", ".join(LAYER_PROPERTIES), path.name))
        if not has_paste:
            _log("    Layer.pasteProperties not present: requires ArcGIS Pro 3.4+ to apply", "warning")
    target_ids = [getattr(p["target"], "URI", id(p["target"])) for p in plans]
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("Two approved standards match the same analyst layer. "
                         "Resolve the duplicate registry/alias mapping before applying.")
    if not apply:
        _log("PREVIEW: {} layers would be restyled; {} standards absent/skipped. "
             "This is a compatibility preview, not a pixel-level diff.".format(len(plans), len(skipped)))
        return {"mode": "preview", "matched": len(plans), "skipped": skipped}
    if not plans:
        _log("Nothing to apply: no approved layer standards found in this project.", "warning")
        return {"mode": "apply", "updated": 0, "skipped": skipped}

    backup = _backup_project(project, backup_folder, "layer_sync")
    snapshots = []
    for p in plans:
        target = p["target"]
        snapshots.append({"layer": target, "cim": target.getDefinition(CIM_VERSION),
                          "name": target.name, "data_source": str(target.dataSource),
                          "uri": getattr(target, "URI", None),
                          "labels": target.showLabels if target.supports("SHOWLABELS") else None})
    applied = []
    try:
        for p, old in zip(plans, snapshots):
            target, approved = p["target"], p["approved"]
            applied.append(old)
            target.pasteProperties(approved, LAYER_PROPERTIES)
            if target.supports("SHOWLABELS") and approved.supports("SHOWLABELS"):
                target.showLabels = approved.showLabels
            # Change the layer name only AFTER its styling succeeded.
            target.name = p["entry"]["standard_layer_name"]
            if str(target.dataSource) != old["data_source"]:
                raise RuntimeError("Data source changed unexpectedly for {}".format(target.name))
            if getattr(target, "URI", None) != old["uri"]:
                raise RuntimeError("Layer URI changed unexpectedly for {}".format(target.name))
            _log("    Updated {} in {} (data source retained)".format(target.name, p["map"].name))
        project.save()
    except Exception:
        _log("Layer synchronization failed; attempting in-memory rollback. "
             "Backup: {}".format(backup), "error")
        for old in reversed(applied):
            try:
                old["layer"].setDefinition(old["cim"])
                old["layer"].name = old["name"]
                if old["labels"] is not None:
                    old["layer"].showLabels = old["labels"]
            except Exception as exc:
                _log("Layer rollback failed: {}".format(exc), "error")
        _log("Close WITHOUT saving if changes remain; reopen the backup .aprx.", "error")
        raise
    result = {"mode": "apply", "updated": len(plans), "skipped": skipped,
              "backup": str(backup), "standards_folder": str(folder),
              "applied_at": datetime.datetime.now().astimezone().isoformat(),
              "tool_version": VERSION}
    _record_history(backup, "layer", result)
    _log("SUCCESS: synchronized {} standardized layers; {} missing/skipped. "
         "Visually inspect symbol classes and labeling.".format(len(plans), len(skipped)))
    return result


def promote_layer(folder, map_name, layer_long_name, mode="Preview only", backup_folder=None):
    """Project Lead publishes existing feature layer as an approved standard.

    Three modes: Preview only, Publish new standard, Replace existing standard.
    Current map layer is renamed STD_* in memory for the exported layer;
    the original Master .aprx is never saved by this tool.
    Required ACL/permission separation belongs to Windows network share.
    """
    project = _current_project()
    map_obj = _unique_map(project, map_name)
    layer = _layer_by_long_name(map_obj, layer_long_name)
    source_name = layer.name
    std_name = _standard_name(source_name)
    profile = _layer_profile(layer)
    # ArcPy may return distinct Python wrappers for the same map layer on
    # separate listLayers() calls; object identity ("is") is not reliable.
    selected_uri = getattr(layer, "URI", None)
    selected_path = _layer_contents_path(layer)
    duplicates = [lyr for lyr in map_obj.listLayers()
                  if getattr(lyr, "name", None) == std_name
                  and not ((selected_uri is not None and
                            getattr(lyr, "URI", None) == selected_uri) or
                           (selected_uri is None and
                            _layer_contents_path(lyr) == selected_path))]
    if duplicates:
        raise ValueError("Another layer in map {!r} is already named {!r}; "
                         "resolve the naming conflict before publishing.".format(
                             map_name, std_name))
    ident = _safe_slug(map_name).lower() + "__" + _safe_slug(std_name).lower()
    folder = Path(folder)
    registry = _read_registry(folder, required=False)
    known = [e for e in registry["standards"] if e["id"] == ident]
    if len(known) > 1:
        raise ValueError("Duplicate registry entries for {}".format(ident))
    current = known[0] if known else None
    if current:
        if current["map_name"] != map_name or current["standard_layer_name"] != std_name:
            raise ValueError("Standard ID collision; choose a distinct layer/map name")
        if (current["dataset_name"].lower() != profile["dataset_name"].lower() or
                current["geometry_type"].lower() != profile["geometry_type"].lower()):
            raise ValueError("Cannot replace a standard with different feature-class identity/geometry")
        # Retain original aliases and group path so old analyst copies still match.
        original_name = current["source_layer_name"]
        original_long_name = current.get("source_layer_long_name", _layer_contents_path(layer))
    else:
        original_name = source_name
        original_long_name = _layer_contents_path(layer)
    layer_path = _layer_contents_path(layer)
    group_prefix = layer_path.rsplit("\\", 1)[0] + "\\" if "\\" in layer_path else ""
    standard_long_name = group_prefix + std_name
    rel = (Path(APPROVED_DIR) / ( _safe_slug(map_name) + "__" + _safe_slug(std_name) + ".lyrx")).as_posix()
    if current and current["layer_file"] != rel:
        raise ValueError("Existing standard uses a different file path. Rename/migration is not automatic.")
    target_file = _approved_file(folder, rel)
    next_version = int(current.get("revision", 1)) + 1 if current else 1
    _log("Cartographic Standards Manager v{} | PROMOTE {}".format(VERSION, mode))
    _log("Source: {} / {} | Published name: {}".format(map_name, layer_long_name, std_name))
    _log("Dataset: {} ({}) | fields checked: {}".format(
        profile["dataset_name"], profile["geometry_type"], len(profile["required_fields"])))
    _log("Output: {} | revision {}".format(target_file, next_version))
    if mode == "Preview only":
        _log("PREVIEW: would {} this standard. No project or file changed.".format(
            "replace" if current else "publish"))
        return {"mode": "preview", "id": ident, "exists": bool(current)}
    if mode not in ("Publish new standard", "Replace existing standard"):
        raise ValueError("Unknown promotion mode: {}".format(mode))
    if mode == "Publish new standard" and (current or target_file.exists()):
        raise ValueError("Standard already exists; use Replace existing standard to explicitly approve an update")
    if mode == "Replace existing standard" and not current:
        raise ValueError("No existing approved standard to replace; use Publish new standard")
    if not folder.is_dir():
        raise FileNotFoundError("Standards folder must be created by the Project Lead first: {}".format(folder))
    target_file.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lead's full project safe before any in-project naming change.
    backup = _backup_project(project, backup_folder, "standard_publish")
    temp_file = target_file.with_name(target_file.stem + "." + uuid.uuid4().hex + ".lyrx")
    prior_registry = (folder / REGISTRY_FILE).read_bytes() if (folder / REGISTRY_FILE).exists() else None
    archive_old = None
    old_name = layer.name
    published = False
    registry_written = False
    try:
        # Source dataset path in the .lyrx is for reference only; sync NEVER
        # repoints the analyst layer to this published data connection.
        layer.name = std_name
        arcpy.management.SaveToLayerFile(layer, str(temp_file), "ABSOLUTE")
        if not temp_file.is_file():
            raise RuntimeError("SaveToLayerFile produced no .lyrx file")
        saved_layers = arcpy.mp.LayerFile(str(temp_file)).listLayers()
        if len(saved_layers) != 1 or not _feature_layer(saved_layers[0]):
            raise RuntimeError("Published layer file did not contain exactly one feature layer")
        checksum = _sha256(temp_file)
        entry = {"id": ident, "map_name": map_name,
                 "source_layer_name": original_name,
                 "source_layer_long_name": original_long_name,
                 "standard_layer_name": std_name,
                 "standard_layer_long_name": standard_long_name,
                 "layer_file": rel, "sha256": checksum,
                 "geometry_type": profile["geometry_type"],
                 "dataset_name": profile["dataset_name"],
                 "required_fields": profile["required_fields"],
                 "properties": list(LAYER_PROPERTIES), "revision": next_version,
                 "published_at": datetime.datetime.now().astimezone().isoformat()}
        if current:
            registry["standards"] = [entry if e["id"] == ident else e
                                     for e in registry["standards"]]
        else:
            registry["standards"].append(entry)
        registry["standards"].sort(key=lambda e: e["id"])
        if target_file.exists():
            archive = folder / "Archive"
            archive.mkdir(parents=True, exist_ok=True)
            archive_old = archive / (target_file.stem + "_rev{}__{}.lyrx".format(
                current.get("revision", "unknown") if current else "unregistered",
                datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")))
            shutil.copy2(target_file, archive_old)
        os.replace(str(temp_file), str(target_file))
        published = True
        _write_json_atomic(folder / REGISTRY_FILE, registry)
        registry_written = True
        # NEVER save the Lead's CURRENT .aprx during promotion. Publishing a
        # .lyrx and registry is independent of deciding to save/discard edits.
        # The in-memory Contents name becomes STD_*, but the Lead decides
        # whether to persist that rename (File > Save) or discard it on close.
    except Exception:
        _log("Publication incomplete. Attempting to restore earlier files and layer name.", "error")
        try:
            layer.name = old_name
            if published:
                if archive_old and archive_old.is_file():
                    shutil.copy2(archive_old, target_file)
                elif target_file.exists():
                    target_file.unlink()
            if registry_written:
                if prior_registry is None:
                    (folder / REGISTRY_FILE).unlink(missing_ok=True)
                else:
                    temp_reg = folder / (REGISTRY_FILE + ".restore." + uuid.uuid4().hex)
                    temp_reg.write_bytes(prior_registry)
                    os.replace(temp_reg, folder / REGISTRY_FILE)
        except Exception as exc:
            _log("Automatic publication rollback incomplete: {}. "
                 "Use the project backup and inspect standards folder.".format(exc), "error")
        raise
    finally:
        if temp_file.exists():
            temp_file.unlink()
    result = {"id": ident, "revision": next_version, "approved_file": str(target_file),
              "sha256": checksum, "backup": str(backup), "tool_version": VERSION}
    _record_history(backup, "promotion", result)
    _log("SUCCESS: approved {} rev{}. Lead layer renamed {} in memory ONLY. "
         "The master .aprx was NOT saved. Save manually to retain the rename, "
         "or close without saving to discard project changes. "
         "Published .lyrx and registry changes are retained either way.".format(
             ident, next_version, std_name))
    return result


# ---------------------------------------------------------------------------
# ArcGIS Pro toolbox UI. Layout name and prefix remain user parameters.
# ---------------------------------------------------------------------------

def _param(display_name, name, datatype="GPString", required=True):
    return arcpy.Parameter(displayName=display_name, name=name,
                           datatype=datatype,
                           parameterType="Required" if required else "Optional",
                           direction="Input")


def _mode_param(options):
    p = _param("Mode", "mode")
    p.filter.type = "ValueList"
    p.filter.list = options
    p.value = options[0]
    return p


class Toolbox(object):
    def __init__(self):
        self.label = "Cartographic Standards Manager (v0.4.2 POC)"
        self.alias = "CartographicStandards"
        self.tools = [SynchronizeLayout, SynchronizeApprovedLayers, PromoteStandardLayer]


class SynchronizeLayout(object):
    def __init__(self):
        self.label = "1. Synchronize Layout from Master PAGX"
        self.description = "Update the named analyst layout from a master .pagx, synchronizing elements with the chosen prefix."
        self.canRunInBackground = False

    def getParameterInfo(self):
        pagx = _param("Master layout (.pagx) — shared folder", "master_pagx", "DEFile")
        pagx.filter.list = ["pagx"]
        layout_name = _param("Existing analyst layout name (exact match)", "layout_name")
        layout_name.value = "Standard_Layout"
        prefix = _param("Standardized element name prefix", "prefix")
        prefix.value = STANDARD_PREFIX
        return [pagx,
                layout_name,
                prefix,
                _mode_param(["Preview only", "Apply and save (creates backup)"]),
                _param("Backup folder (optional)", "backup_folder", "DEFolder", False)]

    def execute(self, parameters, messages):
        return sync_layout(parameters[0].valueAsText,
                           layout_name=parameters[1].valueAsText,
                           prefix=parameters[2].valueAsText,
                           apply=parameters[3].valueAsText == "Apply and save (creates backup)",
                           backup_folder=parameters[4].valueAsText or None)


class SynchronizeApprovedLayers(object):
    def __init__(self):
        self.label = "2. Synchronize Approved Layer Styles"
        self.description = ("Apply approved .lyrx symbology and labels to matching existing layers. "
                            "Original data connections stay intact. ArcGIS Pro 3.4+.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        return [_param("Standards folder (contains layer_standards.json)", "standards_folder", "DEFolder"),
                _mode_param(["Preview only", "Apply and save (creates backup)"]),
                _param("Backup folder (optional)", "backup_folder", "DEFolder", False)]

    def execute(self, parameters, messages):
        return preview_or_sync_layers(
            parameters[0].valueAsText,
            apply=parameters[1].valueAsText == "Apply and save (creates backup)",
            backup_folder=parameters[2].valueAsText or None)


class PromoteStandardLayer(object):
    def __init__(self):
        self.label = "3. Promote Layer to Approved Standard (Project Lead)"
        self.description = ("Publish an existing grouped or ungrouped feature layer as an "
                            "approved STD_*.lyrx. The Master .aprx is NEVER saved by this tool; "
                            "the Lead decides whether to save the in-memory rename.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        folder = _param("Standards folder (shared)", "standards_folder", "DEFolder")
        map_p = _param("Map name", "map_name")
        layer_p = _param("Layer in Contents (name or group path)", "layer_long_name")
        modes = _mode_param(["Preview only", "Publish new standard", "Replace existing standard"])
        backup = _param("Backup folder (optional)", "backup_folder", "DEFolder", False)
        return [folder, map_p, layer_p, modes, backup]

    def updateParameters(self, parameters):
        # For convenience; exact-match validation in execute is authoritative.
        try:
            project = arcpy.mp.ArcGISProject("CURRENT")
            names = [m.name for m in project.listMaps()]
            if names:
                parameters[1].filter.type = "ValueList"
                parameters[1].filter.list = names
                if not parameters[1].valueAsText:
                    parameters[1].value = names[0]
            map_name = parameters[1].valueAsText
            if map_name:
                maps = [m for m in project.listMaps() if m.name == map_name]
                if len(maps) == 1:
                    layer_names = [getattr(l, "longName", l.name) for l in maps[0].listLayers()
                                   if _feature_layer(l)]
                    if layer_names:
                        parameters[2].filter.type = "ValueList"
                        parameters[2].filter.list = layer_names
        except Exception:
            # Tool validation must remain usable while a project opens.
            pass

    def execute(self, parameters, messages):
        return promote_layer(parameters[0].valueAsText,
                             parameters[1].valueAsText, parameters[2].valueAsText,
                             parameters[3].valueAsText,
                             parameters[4].valueAsText or None)
