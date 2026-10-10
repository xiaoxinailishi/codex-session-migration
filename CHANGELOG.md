# Changelog

## 0.1.0 — 2026-10-10

Initial public version of the generalized migration workflow:

- Read-only selected session export and payload scope/SHA verification.
- Exact user image recovery from existing files or transmitted base64.
- Recipient database preparation, desktop project assignment, JSONL offset rebasing and unchanged-row checks.
- Offline guarded application, error recovery, and manual rollback protected against later work, including WAL changes.
- One-way refresh with previous-export baseline and recipient divergence checks.
- Optional peer-restricted, short-lived LAN transfer and resume.
- Explicitly selected temporary-file cleanup; rollback backups kept by default and live images protected.
- Chinese/English documentation, de-identified troubleshooting, full-home relocation guidance and Windows helpers.
- Synthetic regression tests and Windows/Linux CI.

The generalized tool is a new implementation based on methods exercised in an actual migration. It does not claim every Codex version or native UI has been tested. No private migration artifacts or original conversations are distributed.
