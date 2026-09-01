# GitHub Deploy Edition V4

Build date: 2026-09-01

Changes from live mobile testing:

- Driver outbound quantity remains visible after saving.
- Driver can edit outbound quantity until the branch signs.
- Branch correction screen always allows editing all three quantity fields: documents, outbound books, inbound books.
- Driver can request another correction after a branch correction; correction cycles can repeat until the driver confirms the stop.
- Pending correction prevents duplicate simultaneous requests.
- Correction history is append-only: each request is a separate row.
- Test deployment keeps only the first 3 branches active; the other seeded branches remain stored but inactive for later reactivation.
- Existing hosted SQLite databases are migrated automatically from the old one-correction schema.
