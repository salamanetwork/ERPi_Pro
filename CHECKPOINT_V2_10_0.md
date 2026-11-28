> **[Historical Snapshot — Superseded by `CHECKPOINT_V2_11_0.md` & `SESSION_HANDOFF_V2_11_0.md`]**

# ERPi Pro v2.10.0 — Audit & Release Checkpoint

- **Date:** 2026-11-26
- **Branch / Tag:** `master` / `dev/v2.10.0` (`tag: v2.10.0`, base audit commit `7d15f5f`)
- **Backend Test Suite:** `92 tests OK` (`python3 manage.py test`, 0 issues)
- **Frontend Build:** `npm run build` passed (`check:i18n` across 9 locales / 1,986 paired translations, `vue-tsc --noEmit` 0 errors, `vite build` `dist/` compiled, `collectstatic` updated)

## 1. Engineering Guarantees Enforced Across All 24 Modules

1. **Deterministic Lock Ordering (`select_for_update`):** Enforced hierarchical locking (`Ledger` -> `Site` -> Parent Document -> Child Lines/Entries) inside atomic transactions to eliminate deadlocks under concurrent writes.
2. **Active Entity & Scope Guards:** Validated active status (`Site.active`, `PublicationSpace.active`, `Party.active`, `Location.active`, and active accounts/periods) before mutations, postings, or transitions.
3. **Concurrency & Idempotency:** Protected counters, billing/invoice sequences, asset transfers, part returns, webhook deliveries, and `UploadReceipt` records against race conditions and duplicate requests.
4. **Domain & Lifecycle Integrity:** Prevented invalid state transitions (such as cancelling billed schedules or mutating closed/posted records) and synchronized posting caches and GL accounts.

## 2. Audited Modules & Commit History

| Commit | Scope | Key Audit & Release Fixes |
| :--- | :--- | :--- |
| `a6cb04a` | Core ERP (`accounting`, `inventory`, `manufacturing`, `sales`, `purchasing`, `pos`) | Unified locking, posting cache sync, explicit POS GL accounts, and domain guards |
| `62b5cbb` | `assets`, `workorders` | Atomic asset transfer locks, part return locking, and active domain guards |
| `420760c` | `hr`, `crm` | Lock ordering, active site/entity guards, and closed-record checks |
| `5bfce38` | `hr`, `crm`, `supply`, `parties` | Lock ordering, active location/item/party guards, and state validations |
| `30e5e79` | `projects`, `billing_core` | Site/party lock ordering, active guards, and subtask/counter integrity |
| `7d7e532` | `bank_reconciliation`, `expenses`, `subscriptions` | Ledger/site lock ordering, active guards, line locking, and cancel UX |
| `7f03e8a` | `scheduling`, `helpdesk`, `marketing`, `publication`, `media_service` | Active site/space locking, billed-schedule guards, and `UploadReceipt` idempotency |
| `d02255d` | Root cleanup | Removed stray zero-byte files (`erp-backbone-ui@0.1.0`, `node`, `npm`) |
| `81854a0` | `master` merge (`v2.10.0`) | Merged `dev/v2.10.0` (145 files) into `master` and updated `v2.10.0` tag |
| `7d15f5f` | Frontend cleanup & sync | Removed stray zero-byte files in `frontend/` and aligned `master` and `dev/v2.10.0` |

## 3. Release Verification Checklist

- [x] Full logic, concurrency, and domain audit across all 24 backend modules
- [x] Backend test suite passed (`92/92 OK`)
- [x] Frontend i18n check (`1,986` entries across 9 locales), `vue-tsc --noEmit`, and `vite build` passed
- [x] Django `collectstatic --noinput` completed
- [x] Stray zero-byte artifacts removed and working tree clean
- [x] Merged into `master` and tagged `v2.10.0`
