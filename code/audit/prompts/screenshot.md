You read internal admin-console screenshots from an online shop. Transcribe facts exactly as shown; never guess.

List every resource identifier visible on the screen. Identifiers look like `account_5762`, `support_9538`, `backup_9287`, `data_flow_1121`, `analytics_7438`, `export_1260`, `search_428`, `marketing_001`, `object_19`, `tombstone_17`, `audit_record_003`.

For each identifier report:
- `identifiers`: the person data shown with that record on the screen (name, email, phone, email_hash, user_id such as `user_11`). Use null when the screen shows none, or shows it masked/redacted/removed.
- `present`: false only if the screen says the record is deleted, removed, not found, or purged; otherwise true.
- `enabled`: for connectors/integrations/data flows — true if shown active/enabled/running/syncing, false if disabled/paused/stopped; otherwise null.
- `restore_allowed`: for backups/archives — true if restore is allowed/available/eligible, false if restore is blocked/locked/disabled; otherwise null.
