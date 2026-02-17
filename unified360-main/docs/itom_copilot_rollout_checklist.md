# ITOM + Copilot Rollout Checklist

## 1. Database Migrations
- Create migration for new tables:
  - `copilot_audit_log`
  - `itom_graph_layout`
  - `runbooks`
  - `remediation_actions`
  - `report_schedules`
  - `report_narratives`
- Apply migrations in staging first.
- Validate table indexes and FK constraints after upgrade.

## 2. RBAC/Bootstrap
- Re-run `bootstrap.py` in staging/prod to seed:
  - `copilot.use`
  - `copilot.run_reports`
  - `copilot.remediate`
- Validate role-permission mapping for:
  - `admin`
  - `noc`
  - `readonly`
  - `devops`

## 3. Config and Runtime Checks
- Confirm `PROMETHEUS_URL`, `INFLUXDB_URL`, `INFLUXDB_DB` are reachable.
- Verify report generator dependencies are installed in runtime.
- Verify static assets are served:
  - `static/copilot.js`
  - `static/copilot.css`
  - `static/itom_applications.js`
  - `static/itom.css`

## 4. Functional Smoke Tests
- Copilot:
  - Open panel from layout and ask summary query.
  - Ask report query and run using `Run Report Now`.
  - Confirm permission-denied behavior for users missing Copilot permissions.
- ITOM:
  - Create app/service/dependency.
  - Run `Binding Quality`.
  - Run `Suggestions` and apply one suggestion.
  - Save layout and verify shared layout reload.
- Remediation:
  - Create runbook.
  - Create suggestion action.
  - Approve action.
  - Execute action in dry-run mode.
- Report AI:
  - Create schedule.
  - Generate narrative.
  - List narratives.

## 5. Operational Monitoring
- Track Copilot and remediation API error rates.
- Review `copilot_audit_log` for action traceability.
- Watch DB growth for narrative/audit tables and set retention policy.

## 6. Deployment Sequence
1. Deploy code to staging.
2. Run migrations + bootstrap.
3. Execute smoke tests and permission tests.
4. Deploy to production during low-traffic window.
5. Run post-deploy smoke tests.
6. Monitor for 24 hours, then enable broader user access.

## 7. Rollback Plan
- If critical issue:
  - Disable Copilot launcher in layout template.
  - Revoke Copilot permissions from roles.
  - Keep existing monitoring/report routes unaffected.
- Roll back app release to previous tag.
- Keep DB tables; do not drop during rollback unless explicitly planned.
