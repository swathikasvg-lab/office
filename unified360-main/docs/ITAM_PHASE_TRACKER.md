# ITAM Module Phase Tracker

This tracker is the execution baseline from Phase 1 through Phase 4.  
Status values: `done`, `in_progress`, `pending`.

## Phase 1 - Core Schema + Ingest + Inventory APIs
- `done` Core asset, identity, source, software, relation, discovery run models.
- `done` Base ingest pipeline with supported sources (`servers_cache`, `desktop_cache`, `snmp`, `cloud_manual`).
- `done` Inventory and summary APIs.
- `done` Manual and policy-based discovery run APIs.

## Phase 2 - Reconciliation + Classification + Dedup + Tags/Lifecycle
- `done` Weighted identity matching for dedup candidate selection.
- `done` Confidence-aware golden record field updates.
- `done` Canonical submodels: hardware, network interface, tag, lifecycle.
- `done` Ingest reconciliation writes to canonical submodels.
- `done` Tags API:
  - `GET /api/itam/assets/<id>/tags`
  - `PUT /api/itam/assets/<id>/tags`
- `done` Lifecycle API:
  - `GET /api/itam/assets/<id>/lifecycle`
  - `PUT /api/itam/assets/<id>/lifecycle`
- `done` Schema safety helper for new Phase 2 tables (`ensure_phase2_schema`).
- `done` Full identity-graph merge of multiple matched assets into one canonical record.

## Phase 3 - ITOM Graph Integration + UI + Dashboards + Coverage Analytics
- `done` Initial ITAM -> ITOM binding endpoint.
- `done` Auto-suggest monitor coverage gaps for discovered assets.
- `done` Feed dependency map with ITAM relationship data.
- `done` ITAM/ITOM unified graph and coverage dashboard pages.
- `done` Coverage KPI APIs (bound vs unbound, monitored vs unmonitored).

## Phase 4 - OT Connectors + Cloud Maturity + Policy/Compliance
- `done` OT discovery connectors with protocol-native collectors:
  - `ot_seed`, `ot_manual`
  - `ot_modbus`, `ot_bacnet`, `ot_opcua`
- `done` Cloud-native connectors for AWS/Azure/GCP APIs:
  - `cloud_aws`, `cloud_azure`, `cloud_gcp`
- `done` Cloud integration profile admin APIs/UI (`/api/itam/integrations` + ITAM page modal).
- `done` Compliance policy model + evaluation engine APIs.
- `done` Compliance and lifecycle risk reporting APIs/UI:
  - `/api/itam/risk/summary`
  - `/api/itam/risk/assets`
  - `/api/itam/drift/alerts`
- `done` Cross-domain reconciliation quality scoring and drift alert generation.

## Remaining Hardening (Post-Phase)
1. Add protocol-specific deep collectors (full register/object browsing) for OT sources.
2. Add drift alert acknowledgement/suppression workflow.
3. Add historical trend storage for risk/quality KPIs.

## Enterprise Program Plan
- See `docs/ENTERPRISE_TARGET_PLAN.md` for the full enterprise hardening roadmap, risk remediation matrix, owners, and milestone dates.
