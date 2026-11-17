# ArcGIS Pro Layout Standards Manager — v0.3.0 proof of concept

**No Git required.** The Project Lead authors a shared `Standard_Layout.pagx`; analysts run a Python toolbox inside their separate ArcGIS Pro projects to synchronize **existing** standardized elements in place. Analyst maps and layer content are retained.

**Status:** Offline tests simulate CIM objects and project events; the non-copyable ArcPy geometry fix still requires validation in ArcGIS Pro. This is a prototype. Test exclusively in duplicated projects before moving to production. See "Known limits" below.

## What changed from v0.2

v0.2 copied only predefined CIM style fields. v0.3 instead obtains the Project Lead's **full individual element CIM definition**, then restores specific analyst-owned references before calling `setDefinition()` on the existing analyst element. ArcPy returns detached definitions; the tool requests fresh definitions instead of using Python's `deepcopy`, which cannot copy some ArcPy geometry objects. This approach can carry previously missed nested properties, including scale-bar marks, line widths and colors, neatline symbols, legend fonts, text symbols, backgrounds and borders, without extending a style allowlist.

It does **not** replace an entire layout or the analyst's map objects. The complete-CIM method remains a controlled prototype because the ArcGIS Pro CIM has dependencies: element and symbol types must be compatible, linked content must remain valid, and some changes may still require special handling.

### Source of truth vs. retained analyst content

| Existing `STD_` element | Taken from master | Kept from analyst |
|---|---|---|
| Text (e.g. title, subtitle) | Full element CIM including text, geometry, font and colors | Name, metadata used for local identification |
| Graphic / neatline | Full element CIM, including all nested symbol layers, stroke and fill | Name / identity metadata |
| Scale bar / north arrow | Full CIM including detailed mark and symbol settings | `mapFrame` association |
| Legend | Master's full overall CIM styling and layout settings; item appearance from a compatible master legend item | `mapFrame`, `excludedLayers`, existing analyst item list, each item's linked `layer`, name, visibility, layer-specific group filtering and manual column information |
| Map frame | Geometry, fill, outline, border and most other master CIM properties | `uRI`, `view`, `autoCamera`, grids, extent indicators and layer visibility overrides; linked map / layer set |
| Picture / logo | Full element CIM including picture and placement | Name / identity metadata |

Protected values are transferred from a detached analyst definition into the detached master definition before changing the analyst element. Separate fresh master definitions provide independent legend item styles, and fresh analyst definitions serve as rollback snapshots. If the required CIM type or reference cannot be matched, the entire run fails in preflight (before edits), rather than silently applying only part of a style. The toolbox makes a pre-sync backup for Apply operations and attempts an in-memory rollback on failure.

**Important legend behavior:** A local legend item is styled from a master item of the same CIM subtype, matching its name when unique. Otherwise, the first compatible master item or compatible `defaultLegendItem` supplies the style. The local item still points to its own analyst layer. If you want different standardized formatting for different legend-item types, include representative items for those types in the master. A fallback to a different layer name of the same CIM type may not be the right design for every legend; inspect rendered legends. Feature-layer symbology and patch appearance derived from an analyst's actual map layers are not overwritten.

## Installation — replacing your v0.2 toolbox

1. Make a **working copy** of your `AnalystLayoutTest.aprx` and keep your current v0.2 file somewhere safe.
2. Extract this package, and place `LayoutSync.pyt` in your existing `Layout Standards Manager` folder. You can overwrite the older `.pyt` if you have backed it up.
3. In ArcGIS Pro, right-click the toolbox in Catalog and choose **Refresh**. Reopen the geoprocessing tool if its panel is already open.
4. Open the analyst test `.aprx`. Confirm that `Standard_Layout` exists, with all elements named exactly as in the master. Any element controlled by the lead must start with `STD_`. Do **not** group standardized elements for this version.
5. Have the lead edit the master `.aprx` and export its layout again as `Standard_Layout.pagx`. Do **not** simply save the `.aprx`: the `.pagx` must reflect the latest changes.
6. Run **Preview only** first, pointing to the newly exported `.pagx`, layout `Standard_Layout`, prefix `STD_`.
7. Review the messages and, if preflight succeeds, select **Apply and save (creates backup)**. Choose a local backup folder, e.g. `C:\LayoutSync_Backups`.
8. Inspect the layout itself and export a PDF to confirm changes render as expected. Inspect both the Contents pane and the map extent/scale. The script also checks several link, item and camera invariants before saving.

### Recommended next test — your observed scale-bar discrepancy

| Item | Set on the master | Expected result on the analyst copy |
|---|---|---|
| `STD_ScaleBar` | Divisions / marks length **5 pt** | 5 pt marks after sync |
| `STD_ScaleBar` | Change tick marks to red | Matching tick color after sync |
| `STD_ScaleBar` | Change scale-line width and color | Matching line thickness and color |
| `STD_NorthArrow` | Use red symbol | Matching red symbol |
| `STD_Legend` | Bold labels, change item spacing | Bold labels and master spacing; analyst's existing layer items retained |
| `STD_LegendBox` | Change neatline color and thickness | Matching outline styling |
| `STD_MainMapFrame` | Change frame border only | Matching border; local map and camera kept |

Your previous test reported standardized elements `STD_DynamicTitle`, `STD_Filepath`, `STD_KeyMapFrame`, `STD_Legend`, `STD_LegendBox`, `STD_Logo`, `STD_MainMapFrame`, `STD_MapSubtitle`, `STD_NorthArrow`, `STD_ScaleBar`. These names are all supported if the CIM classes match and the elements are ungrouped.

## Suggested network directory (no Git)

```text
\\spatialfiles.bcgov\...\Layout Standards Manager\
    Standard_Layout.pagx      <-- Project Lead exports/publishes
    LayoutSync.pyt            <-- analyst synchronization tool
    version.json              <-- optional, small metadata file
```

Example optional `version.json`:

```json
{
  "version": "0.3-pilot",
  "published_by": "Project Lead",
  "description": "Approved cartographic standards"
}
```

The toolbox always computes the **SHA-256 hash** of the `.pagx` so it is possible to identify the exact master even if `version.json` is not maintained. Audit records are added to a JSONL file in the backup folder after successful application.

## Safety and validation behaviors

- Apply requires a saved, editable `.aprx` opened as `CURRENT` in ArcGIS Pro.
- Page size and page units must match the master; the toolbox does not resize a layout page.
- **No elements are created, deleted, renamed, reordered, or regrouped.** All `STD_` element names and element types must match between master and analyst layouts.
- Source and destination element CIM subtype must match exactly (e.g., `CIMScaleLine` to `CIMScaleLine`).
- Required linked field values are preserved. Missing mandatory CIM properties or incompatible legend-item subtypes stop the operation.
- Preview builds the plan in memory without modifying the project; it lists some straightforward attribute differences but **is not an exhaustive CIM color/symbol diff**.
- Apply creates an `.aprx` backup with `saveACopy()` before updating elements. It then applies the full master-derived CIM to each existing element, verifies basic linked content/camera invariants, and saves the analyst `.aprx`.
- On an error the script tries to restore each affected element's earlier CIM. If the ArcGIS UI still shows modifications, **close without saving** and reopen the project; use the pre-sync backup if needed.
- The backup is a copy of the `.aprx`, **not** a copy of its referenced databases or image resources.

## Known limits — read before use

1. **Not yet real-world validated.** Offline tests simulate CIM objects and project events, not the entire ArcGIS Pro renderer/serialization environment. Full-CIM replacement may expose previously unknown element-specific dependencies, and runtime adjustments may be needed.
2. **Legend contents are different.** The tool preserves the analyst's item membership, layer references, and explicit excluded layers. It does not replace or restyle the feature layers' map symbology. A master item style selected by same-type fallback may not suit every analyst layer. After synchronizing, inspect mixed-type legends and any legend that overflows its frame.
3. **Camera and map-frame resizing.** Analysts control their own camera position; the master's camera is not transferred. The script preserves the analyst's CIM `view`/`autoCamera` and restores the analyst's camera after layout changes, including 2D center, heading and scale or 3D position and orientation. It also attempts camera restoration after rollback, and checks camera values before saving. An undefined value reported as `nan` both before and after the update is treated as unchanged. If restoration fails or ArcGIS changes a protected value, the tool fails before saving. A resized frame can show a different geographic extent even at the same center and scale; inspect the result.
4. **Picture assets and fonts.** Picture sources must be accessible (when the layout reports an external `sourceImage`), and fonts used by the lead must be installed on analyst computers. Embedded/custom symbols and linked resources may need separate validation.
5. **Complex properties.** Some CIM elements can contain references beyond the protected fields identified here. For uncommon map surrounds, dynamic texts, map series, special graphics or 3D scenes, additional reference rules may be required; test them rather than assuming universal support.
6. **Layout-level settings and z-order.** v0.3 updates **existing named elements**, not page-level CIM, layout map series, graphics-layer memberships, grouped elements, element z-order, or the arrangement of layer entries in the map. The master `.aprx` remains the baseline for those.
7. **Repeated runs.** Applying the same master multiple times is intended to be stable but must be checked with your actual layouts. Preview alone does not claim zero nested CIM differences.
8. **No automatic distribution.** The project lead manually publishes the `.pagx` to the shared drive; analysts manually run the toolbox. GitHub and selective updates are intentionally excluded.

## Offline test harness

The tests run outside ArcGIS Pro using a fake `arcpy` module:

```powershell
python -m unittest discover -s tests -v
```

They cover master-first symbol/geometry changes, preserved legend items, map references and camera values (including undefined camera ordinates), preview without saving, missing elements, incompatible legend item types, backup/rollback, non-copyable geometry objects, and release hash reporting. They cannot validate Esri's actual CIM property serialization or layout rendering.

## References

- [Esri Python CIM access](https://pro.arcgis.com/en/pro-app/latest/arcpy/mapping/python-cim-access.htm)
- [Esri CIMMapFrame properties](https://doc.esri.com/en/arcgis-pro/latest/sdk/api-reference/topic2901.html)
- [Esri CIMLegend properties](https://doc.esri.com/en/arcgis-pro/latest/sdk/api-reference/topic2691.html)
- [Esri CIMLegendItem properties](https://doc.esri.com/en/arcgis-pro/latest/sdk/api-reference/topic2728.html)
- [Esri ArcGISProject / saveACopy](https://pro.arcgis.com/en/pro-app/latest/arcpy/mapping/arcgisproject-class.htm)
- [Esri ConvertLayoutFileToLayout](https://pro.arcgis.com/en/pro-app/latest/arcpy/mapping/convertlayoutfiletolayout.htm)
