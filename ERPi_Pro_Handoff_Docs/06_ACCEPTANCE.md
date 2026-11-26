# ERPi Pro — Acceptance Criteria & QA Checklist (`v2.10.0`)

## 1. Localization & Directionality Acceptance
- [x] **9 Languages Active:** English (`en`), Arabic (`ar`), Urdu (`ur`), Persian (`fa`), French (`fr`), Spanish (`es`), German (`de`), Turkish (`tr`), Hindi (`hi`).
- [x] **RTL Mirroring:** Selecting `ar`, `ur`, or `fa` flips layout direction (`dir="rtl"`), sidebar alignment, table columns, and form labels cleanly.
- [x] **Zero Hardcoded Leaks:** Sidebar groups, module tabs, table toolbars, command palette (`Ctrl+K`), and `PageGuidance` (`How this screen works`) render in the active language.
- [x] **Automated Gate:** `npm run check:i18n` reports 1986 paired keys with 0 errors.

## 2. Build & Runtime Acceptance
- [x] **Frontend Compilation:** `vue-tsc --noEmit` and `vite build` complete with exit code `0`.
- [x] **Static Serving:** `collectstatic` bundles latest hashed JS/CSS chunks into `staticfiles/`.
- [x] **Backend Health:** `python manage.py check` passes with 0 warnings/errors.

## 3. Functional Smoke-Test Checklist (Manual / E2E)
- [ ] Switch language in **Settings** across RTL (`ar`, `ur`, `fa`) and LTR (`fr`, `es`, `tr`, `hi`, `de`, `en`) and verify hard refresh (`Ctrl+Shift+R`) persistence.
- [ ] Open Command Palette (`Ctrl+K`) and verify localized quick actions and keyboard navigation.
- [x] Execute a complete **POS-01** checkout flow (`POS-01-20261125-0001`, 15,750.00 AED), verify receipt print view, stock movement (`#118`), and balanced GL journal entry (`JE-000178`).
- [x] Create and post a **Goods Receipt (GRN)** and verify stock balance update and accounting journal entry.
- [x] Verify **Bank Reconciliation** statement matching, form validation, `Cancel` action UX, and unreconciled difference calculation.
