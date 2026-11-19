# Changelog

## 0.4.1 — 2026-11-17

- Restored **Existing analyst layout name (exact match)** in Tool 1 (default `Standard_Layout`).
- Restored **Standardized element name prefix** in Tool 1 (default `STD_`).
- Restored the original five-parameter order for Tool 1; tool passes both supplied values into `sync_layout`.
- No changes to layer synchronization, layer promotion, or the CIM merge engine.


## 0.4.0 — 2026-11-17

- Preserved v0.3.1 master-first in-place layout CIM engine.
- Removed layout name and `STD_` prefix from analyst-facing layout sync parameters (automatic matching and internal constant).
- Added `Synchronize Approved Layer Styles`: registry-driven feature-layer symbology + labeling synchronization (`pasteProperties`, ArcGIS Pro 3.4+), naming conventions, compatibility validation, per-project backups, checksums, rollback attempt, and audit records.
- Added `Promote Layer to Approved Standard`: publish new/replacement `.lyrx` from a feature layer already in a Project Lead `.aprx`; automatically produce `layer_standards.json`, preserve old original name as alias, revision history and archived layer file.
- Supports analysts' shared or separately copied geodatabase workspaces when map and feature-class names, geometry and schema match.
- Added 15 offline mock tests. Real ArcGIS Pro layer-sync API testing is pending.
