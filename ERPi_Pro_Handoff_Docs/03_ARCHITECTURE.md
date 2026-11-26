# ERPi Pro — System Architecture (`v2.10.0`)

## 1. High-Level Topology
```text
+-------------------------------------------------------------------+
|                        Browser Client (SPA)                       |
|  Vue 3 + TypeScript + Vite | Dynamic RTL/LTR | Custom i18n Engine |
+---------------------------------+---------------------------------+
                                  | REST / JSON (Session + CSRF)
+---------------------------------v---------------------------------+
|                     Django 5.2 Application Layer                  |
|  Auth & RBAC | Multi-Site Scoping | Audit Trail | State Machines  |
+---------------------------------+---------------------------------+
                                  | Django ORM
+---------------------------------v---------------------------------+
|                 Relational Database & File Storage                |
|        SQLite / PostgreSQL | Media Attachments | Print Templates  |
+-------------------------------------------------------------------+
```

## 2. Core Functional Domains
1. **General & Workspace:** Global search (`Ctrl+K` Command Palette), notifications, inbox counters, public portals (`Public Sites`), and customizable sidebar navigation.
2. **Accounting & Budgeting:** Chart of accounts, financial periods, posting rules, journal entries, cost centers, budgets, cutover balances, and bank reconciliation.
3. **Sales, CRM & POS (`POS-01`):** Leads, opportunities, customer master records, quotations/sales orders, AR aging, subscriptions, and rapid cashier checkout (`POS-01`) with receipt printing.
4. **Procurement:** Purchase requisitions, purchase orders, supplier master records, and supplier invoices.
5. **Inventory & Supply Chain:** Item master, warehouses/locations, goods receipts (GRN), stock movements, stock balances, unit conversions, and opening balances.
6. **Manufacturing:** Bills of materials (BOM), production orders, material issue (`mfg_issue`), and finished goods output (`mfg_output`).
7. **Assets & Maintenance:** Fixed assets, maintenance work orders, preventive maintenance schedules, and spare parts issue/return.
8. **HR & Payroll:** Employees, departments/grades, leave requests, time & attendance, offboarding, payroll runs, and payslips.
9. **Administration & Governance:** Module manager, integrations & webhooks (`GatewayPage`), role-based access control, audit trail, document settings, and trash/restore.

## 3. Internationalization (`i18n`) Architecture
- **Core Engine:** `frontend/src/i18n.ts` manages reactive locale state, document direction (`dir="rtl"` vs `dir="ltr"`), persistent user preference, and dictionary lookup via `t(key, params)`.
- **Locale Dictionaries:** `frontend/src/locales/{en,ar,ur,fa,fr,es,de,tr,hi}.json` + `help.json`.
- **Validation Gate:** `frontend/scripts/check-i18n.mjs` enforces key parity across all 9 locales before every production build.
