# ERPi Pro — Acceptance Criteria & QA Checklist (`v2.11.0`)
**Verified:** 2026-11-28 | **Release:** `v2.11.0`

## 1. Release & Print Localization Acceptance (`v2.11.0`)
- [x] **Version Alignment:** `RELEASE_VERSION = "2.11.0"`, `ERP_RELEASE=2.11.0`, and `modules/pos/manifest.json` aligned to `2.11.0`.
- [x] **29 Module Contracts:** `python manage.py audit_modules` passes all 29 contracts with 0 compatibility issues.
- [x] **Company Identity & 9-Locale Print:** `CompanyProfile` (TRN, CR, currency, address) and `backbone/print_i18n.py` render localized `RTL`/`LTR` print templates across all 9 locales.
- [x] **Toast Notice Polish:** Internal `__auto__` marker prevented from rendering in UI toast notices.

## 2. Localization & Directionality Acceptance
- [x] **9 Languages Active:** English (`en`), Arabic (`ar`), Urdu (`ur`), Persian (`fa`), French (`fr`), Spanish (`es`), German (`de`), Turkish (`tr`), Hindi (`hi`).
- [x] **RTL Mirroring:** Selecting `ar`, `ur`, or `fa` flips layout direction (`dir="rtl"`), sidebar alignment, table columns, and form labels cleanly.
- [x] **Zero Hardcoded Leaks:** Sidebar groups, module tabs, table toolbars, command palette (`Ctrl+K`), and `PageGuidance` (`How this screen works`) render in the active language.
- [x] **Automated Gate:** `npm run check:i18n` reports 1,988 paired keys, 77 help topics, and 259 field explanations with 0 errors.

## 3. Build & Runtime Acceptance
- [x] **Frontend Compilation:** `vue-tsc --noEmit` and `vite build` complete with exit code `0`.
- [x] **Static Serving:** `collectstatic` bundles latest hashed JS/CSS chunks into `staticfiles/`.
- [x] **Backend Health:** `python manage.py check` passes with 0 warnings/errors.

## 4. Functional Smoke-Test Checklist (Manual / E2E)
- [ ] Switch language in **Settings** across RTL (`ar`, `ur`, `fa`) and LTR (`fr`, `es`, `tr`, `hi`, `de`, `en`) and verify hard refresh (`Ctrl+Shift+R`) persistence.
- [ ] Open Command Palette (`Ctrl+K`) and verify localized quick actions and keyboard navigation.
- [x] Execute a complete **POS-01** checkout flow, verify receipt print view in active locale, stock movement, and balanced GL journal entry.
- [x] Create and post a **Goods Receipt (GRN)** and verify stock balance update and accounting journal entry.
- [x] Verify **Bank Reconciliation** statement matching, form validation, `Cancel` action UX, and unreconciled difference calculation.
