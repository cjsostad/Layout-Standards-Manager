# Cartographic Standards Manager — v0.4.2 proof of concept

ArcGIS Pro Python toolbox (`LayoutSync.pyt`) for a Project Lead + GIS analysts. **No GitHub is required.** This release fixes grouped layer matching, dataset field validation, and Master project saving in the layer tools, while retaining the v0.3.1 in-place layout CIM engine.

> **IMPORTANT:** The layout engine is based on the v0.3.1 implementation tested by the user in ArcGIS Pro. The new layer-publishing/synchronizing code has passed **23 offline mock tests**, but has **not been run against the real ArcGIS Pro API** here. Test in a *copy* of a real project before deploying to your team.

## Compatibility

- **ArcGIS Pro 3.4 or newer is required for the new layer synchronization**, because it uses `Layer.pasteProperties(source_layer, ["SYMBOLOGY", "LABELING"])`, available in that release and later. The method must run **inside ArcGIS Pro** (Python toolbox, Notebook, or Python window), not in a standalone Python interpreter.
- Original layout synchronization uses the ArcGIS Pro 3.x CIM (`getDefinition("V3")` / `setDefinition()`) and retains the v0.3.1 implementation, with its original analyst-facing layout-name and element-prefix parameters restored.
- Feature layers only for the new layer tools. Raster, group, annotation, scene, standalone tables and web-only special layer types are **not supported** in this first layer-standard release.

## The three tools

| Tool | Who runs it | Purpose |
|---|---|---|
| **1. Synchronize Layout from Master PAGX** | Analysts | Update in-place `STD_` layout elements from approved `.pagx`. The analyst enters the exact layout name and the standardized element prefix (default `STD_`). |
| **2. Synchronize Approved Layer Styles** | Analysts | Paste approved **symbology + labeling** from approved `.lyrx` files into matching layers. Keep each analyst's existing data-source path, layer URI and additional layers; rename matched layers to `STD_...`. |
| **3. Promote Layer to Approved Standard (Project Lead)** | Project Lead | Take an existing feature layer in the open master `.aprx`, give it an `STD_` name, create/update its approved `.lyrx`, and register it for distribution. |

For layout synchronization, the user chooses the element prefix (default `STD_`). For layer-style synchronization, the approved `STD_` naming convention is still internal. Only approved entries in `layer_standards.json` are synchronized: **adding `STD_` to a layer by itself does not register it**. Analysts' other layers remain untouched.

## Suggested shared-folder structure

Create a folder on your shared drive, for example:

```text
CartographicStandards/                      <- read-only for analysts
    LayoutSync.pyt                          <- can also be installed separately
    Standard_Layout.pagx                    <- Project Lead exports this
    layer_standards.json                    <- generated automatically on first promotion
    Approved_Layers/                        <- approved .lyrx created by the tool
        Main_Map__STD_Critical_Habitat.lyrx
        Main_Map__STD_Protected_Areas.lyrx
    Archive/                                <- previous approved layer file revisions
    Working_Layers/                         <- optional existing collaborative library
        Analyst_A_Example.lyrx
        Analyst_B_Example.lyrx
```

**Permissions:** Allow all analysts to read the standards folder. Give write permissions to the Project Lead for `Approved_Layers`, `layer_standards.json` and `Archive`. Your existing collaborative Working Layers folder can retain the team's usual write access. The tool **does not authenticate a Project Lead**; network permissions provide that control.

Place analyst project backup folders **outside** this standards directory, ideally in analyst-owned local project folders.


## v0.4.2 fixes — grouped layers and Lead project saving

**1. No automatic saving of the Master `.aprx` when publishing standards.** Tool 3 no longer calls `ArcGISProject.save()`. The published `.lyrx` and `layer_standards.json` still update. The working Master layer is renamed `STD_...` in the Contents pane until you either save manually or close without saving. A **separate** timestamped `.aprx` backup copy is still created with `saveACopy()` in the specified backup folder (or `LayoutSync_Backups`). This is not an overwrite of the original Master project. Tools 1 and 2 remain analyst synchronization tools and **do save** their analyst projects on Apply.

**2. Grouped feature layers now work in Tool 3 and Tool 2.** Tool 3 accepts either a unique short name (`Wildlife Habitat Areas`) or a full Contents path such as `Environmental Layers\Wildlife Habitat Areas`. The dropdown should list full nested paths. Tool 2 searches all nested feature layers and uses registered full paths to resolve duplicate short names when possible. If the layer is unique by name, its group hierarchy can differ between Lead and Analyst projects.

**3. Fixed your reported `OSError: "Wildlife Habitat Areas" does not exist`.** Field validation no longer passes an `arcpy.mp.Layer` inside a group directly to `arcpy.ListFields()`, which ArcGIS Pro can erroneously interpret as only the layer's display name. It first requests fields from `arcpy.Describe(layer)` or its `dataElement`, then falls back to `arcpy.ListFields(layer.dataSource)` using the full underlying feature-class path. This retains the geometry/schema safety checks for copied geodatabases.

**4. Existing registries continue to work.** New approved entries add `source_layer_long_name` and `standard_layer_long_name` to help match duplicates across groups. v0.4.1 entries without these fields are accepted; if duplicate names make matching ambiguous, republish with v0.4.2 from the Lead Master or resolve the duplicate manually. This prototype still registers one approved standard per **map name + short layer name**, so publishing two distinct standards with the same short layer name in two groups of the same map is not supported yet.

### Targeted retest with Wildlife Habitat Areas

1. Back up your `LayoutSync.pyt`, replace it with v0.4.2, and refresh/reopen the ArcGIS Pro toolbox.
2. In the Master `.aprx`, open Tool 3 and select `Wildlife Habitat Areas` or its full group path from the Contents-path dropdown. **Preview only** first, then **Publish new standard** (or **Replace existing standard** if already published). Verify `.lyrx` and registry output.
3. **Do not save the Master `.aprx`**; verify that after publishing the layer rename is visible but the original `.aprx` has not been written. If you want, close without saving and reopen to confirm the earlier saved project state is restored. The approved standard remains available in the shared folder.
4. In a disposable Analyst `.aprx`, open Tool 2 → **Preview only**. It should locate the grouped layer and read its fields without raising the original error. Then Apply and inspect symbology, labeling, legend items, group position, and data connection.
5. The layout sync tool (Tool 1) still has its **five original parameters**, including *Existing analyst layout name* and *Standardized element name prefix*.

**Scope:** Nested feature layers inside groups are supported, but the **group containers themselves are not standardized or restyled**. Run all three tools on disposable project copies first; ArcPy runtime behavior has not been tested here.

## Installation

1. Back up your existing working `LayoutSync.pyt` (v0.3 or v0.3.1). Keep that working version until v0.4 has passed ArcGIS Pro testing.
2. Copy the new `LayoutSync.pyt` into a local or network directory. It is a **single self-contained toolbox file**; the tests/README are not required at runtime.
3. In ArcGIS Pro: **Catalog > Toolboxes > Add Toolbox**, browse to `LayoutSync.pyt`. If replacing a previously opened file, **Refresh** the toolbox and reopen its tool window.
4. Confirm your ArcGIS Pro version is **3.4+** for new layer synchronization.
5. Ensure the existing master layout has been exported to `.pagx`; updating a Master `.aprx` does **not** update a previously exported `.pagx` automatically.
6. Create the shared standards folder. The Project Lead can publish the first `.lyrx` using tool 3; it generates `layer_standards.json` and `Approved_Layers` automatically.

## Recommended controlled test: promote a layer that has never been exported

Use **copies of your Master `.aprx` and Analyst `.aprx`**, not your production files.

**Project Lead:**

1. Open the **Master `.aprx`**. Select an existing feature layer, such as `Critical_Habitat` in map `Main_Map`. It does **not** need a prior `.lyrx` file.
2. In ArcGIS Pro, style it, e.g. polygon blue-to-green and labels Arial Regular-to-Bold.
3. Run **3. Promote Layer to Approved Standard (Project Lead)**. Select the shared standards folder, `Main_Map`, and the Contents layer (`Critical_Habitat`).
4. Run **Preview only** to inspect proposed file name, geometry, dataset name and registry revision.
5. Change mode to **Publish new standard** and run. A separate `.aprx` backup copy is created. The tool publishes `Approved_Layers/Main_Map__STD_Critical_Habitat.lyrx`, writes its SHA-256 plus metadata to `layer_standards.json`, and renames the layer to `STD_Critical_Habitat` **in the open project only**. **The original Master `.aprx` is NOT saved automatically.** Save manually if you want to keep the rename, or close without saving to restore the project to its last saved state. Publishing the `.lyrx` and registry is a separate action; those files remain published even if you discard project edits.
6. To adjust the approved style later, edit the layer in the Master `.aprx` and use **Replace existing standard**. The previous `.lyrx` is archived and the registry revision increases.

**Analyst:**

1. Open a *copy* of the analyst `.aprx`. Its feature layer may still be named `Critical_Habitat` and may point to a **different copy of `ProjectData.gdb`**.
2. Run **2. Synchronize Approved Layer Styles**. Choose the **standards folder root**, not `Approved_Layers`.
3. Run **Preview only**. Confirm the matched layer, map and standard ID. Nothing is changed by preview.
4. Use **Apply and save (creates backup)**. The script creates an `.aprx` backup, pastes symbology and labels onto the existing layer, sets label visibility to match the approved source when available, renames it to `STD_Critical_Habitat`, checks its data source and URI were not changed, and saves.
5. Verify the updated **fill/outline, label fonts and label placement** in the map and in an exported PDF. Check that the layer still points to the analyst's original geodatabase.

**Important:** If an analyst does not have the standard layer in their project, the tool **reports SKIP**. It will not insert a new layer. If a map named in the registry is missing, the tool stops (rather than guessing a target map).

## How the registry matches layers across geodatabases

An approved entry automatically records:

- **`map_name`**: exact map name shared through the original Master `.aprx`.
- **`source_layer_name`**: original analyst layer name before promotion, for example `Critical_Habitat`.
- **`standard_layer_name`**: project-lead-approved name, for example `STD_Critical_Habitat`.
- **`geometry_type`**: point/polyline/polygon/etc.
- **`dataset_name`**: final feature-class name (not the `.gdb` location).
- **`required_fields`**: non-system fields and their ArcPy field types from the approved layer's underlying dataset.
- **`layer_file` and `sha256`**: approved `.lyrx` location and checksum.
- **`revision` and `published_at`**: release metadata.

Matching requires one unambiguous feature layer with either the original name or `STD_` name in the specified map, matching **geometry, feature-class name, and required field types**. The approved field schema must be a subset of the analyst's. GDB **directory paths may differ**. This prevents accidental transfer of label expressions or class renderers to an incompatible layer, but it does mean intentionally different dataset names are not supported yet. A wrong field alias/label expression that references an *external* resource may still need manual inspection.

**Both supported geodatabase cases:**

```text
Shared:  Lead.aprx -> X:/Projects/SharedData.gdb/Critical_Habitat
         A.aprx    -> X:/Projects/SharedData.gdb/Critical_Habitat

Copied:  Lead.aprx -> X:/Projects/Lead/ProjectData.gdb/Critical_Habitat
         A.aprx    -> X:/Projects/A/ProjectData.gdb/Critical_Habitat
```

The `.lyrx` itself can retain a reference to the Project Lead's source dataset because Esri's layer-file format stores the layer definition. The synchronization method **does not use that data connection to replace the analyst's connection**. Depending on your environment, however, an approved `.lyrx` with an inaccessible/broken source may still behave differently in `pasteProperties()` and must be tested.

## Layout tool: original five-parameter interface (restored in v0.4.1)

**Tool 1** has the same five parameters as v0.3.1, in the same order:

1. Master layout `.pagx` — shared folder
2. Existing analyst layout name (exact match) — default `Standard_Layout`
3. Standardized element name prefix — default `STD_`
4. Mode (`Preview only` or `Apply and save (creates backup)`)
5. Optional backup folder

The tool uses **the entered analyst layout name**, rather than inferring the name from the master. The prefix is applied to layout element names when matching master and analyst elements. These changes affect **only Tool 1**; Tool 2 and Tool 3 continue using the approved-layer registry and internal `STD_` convention. The layout CIM merge engine is otherwise unchanged from v0.4. The tool still requires corresponding standardized element names/types and will not automatically add or delete layout elements. See the v0.3.1 README for CIM/legend caveats.

## Safeguards, scope and limits

- **Preview** validates names, paths, geometry, field schema, checksum and feature-layer suitability. It does not make a per-symbol/per-label detailed visual difference report.
- **Analyst Apply** creates a dated backup `.aprx` with `saveACopy()` before changing the in-memory project, then calls `save()`. Backups do **not** contain copies of geodatabases.
- **Lead Promote/Replace** creates a separate backup `.aprx` with `saveACopy()`, publishes `.lyrx`/registry, and renames the layer in memory, **but never calls `project.save()` on the Master**. The Lead can deliberately save or discard Master project changes later.
- **Rollback** attempts in-memory restoration of layer CIM and original names on failure. It does not guarantee recovery from ArcGIS Pro crashes or partial network write failures. Use the backup `.aprx` if anything looks wrong.
- **Publishing** writes a staged `.lyrx`, validates it, saves the SHA-256 and registry and, for replacements, archives the previous file. Network access loss between file and registry writes may require manual repair.
- **Collision handling**: duplicate matching layer names, incompatible field/geometry schema, duplicate registry IDs, changed `.lyrx` hashes or missing required maps are rejected rather than silently updated.
- **Scope for v0.4:** feature-layer **symbology and labeling** only. Other layer properties like definition queries, display filters, pop-ups, joins, feature selection, visibility and layer order are deliberately not synchronized. A layer's label expressions and classes *are* in the approved labeling and may need the field schema to match.
- The `.lyrx` may reference inaccessible data. In practice, the Project Lead should ideally publish using feature datasets on a path readable to analysts, or the first live pilot should check if ArcPy can paste styling from a broken-source `.lyrx` without a problem.
- No Git integration, network watching, automatic enforcement, role authentication, or auto-addition of missing layers.
- `STD_` is a **human-visible naming convention**, not an ArcGIS permission lock. Analysts can still edit those layers until the next sync.

## Offline tests

The package includes 15 Python `unittest` cases using a small fake ArcPy module. You can run them in normal Python (not ArcGIS Pro):

```bash
python -m unittest discover -s tests -v
```

They check first-time publishing, re-publication with revision and archive, preview no-side-effects, shared/copied GDB matching, field/geometry mismatch detection, ambiguous matches, missing standard skip, hash mismatch, minimum ArcGIS Pro API availability, rollback, and registry path validation. **They do not test ArcGIS Pro's actual `Layer.pasteProperties` or `.lyrx` serialization.** Please send the ArcGIS Pro geoprocessing messages from the first live pilot if any errors or unexpected appearance arise.

## Esri documentation

- [ArcPy `Layer.pasteProperties` (ArcGIS Pro 3.4)](https://pro.arcgis.com/en/pro-app/3.4/arcpy/mapping/layer-class.htm)
- [ArcPy `SaveToLayerFile`](https://pro.arcgis.com/en/pro-app/3.6/tool-reference/data-management/save-to-layer-file.htm)
- [ArcPy `LayerFile`](https://pro.arcgis.com/en/pro-app/3.4/arcpy/mapping/layerfile-class.htm)
- [Python toolbox parameters and validation](https://pro.arcgis.com/en/pro-app/3.5/arcpy/geoprocessing_and_python/defining-a-tool-in-a-python-toolbox.htm)
- [ArcGIS Pro CIM API](https://pro.arcgis.com/en/pro-app/latest/arcpy/mapping/python-cim-access.htm)
