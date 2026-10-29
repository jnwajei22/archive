# Autonomous controls compatibility note

The product authorization source is now the per-user `autonomous_controls` row. The legacy
`execution_mode`, `autonomous_armed`, `armed_until`, and `autonomous_shadow_mode` columns remain
temporarily so older databases and audit readers can be upgraded without a destructive migration.

- Live autonomy always migrates to off.
- Demo autonomy migrates to on only when an enabled legacy profile was durably marked both
  `demo_autonomous` and `autonomous_armed`; otherwise it migrates to off.
- The global autonomous kill switch inherits the durable legacy kill-switch value when present.
- Timed arm/disarm endpoints return a deprecation response and do not authorize execution.
- Legacy shadow values are ignored by production dispatch authorization.

A later cleanup migration may remove the legacy columns after older clients no longer consume
them. Historical runs, submissions, dispatches, and control audit rows must remain immutable.
