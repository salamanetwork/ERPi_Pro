# ERPi Pro — Engineering Foundation & Conventions (`v2.11.0`)

## 1. Core Architectural Principles
- **Explicit State Transitions:** Business documents follow strict lifecycle states (`draft` -> `submitted` -> `approved` -> `issued` / `posted` -> `voided` / `reopened`) enforced on the backend.
- **Multi-Site & Multi-Tenant Scoping:** Operational records are scoped by `site` and protected by granular permission checks (`may('manage')`, `may('issue')`, etc.).
- **Auditability:** Critical mutations record actor metadata (`Created by`, `Issued by`, `Posted by`, `Voided by`, `Reopened by`) and revision numbers to prevent stale overwrites.

## 2. Strict i18n & Print Rules (Mandatory for All Future Code)
1. **Never hardcode UI or Print strings** in Vue templates, TS config objects, or Django print templates. Wrap UI text with `t('English Key')` and route printable labels through `backbone/print_i18n.py`.
2. **Never write bilingual switches** (`locale === 'ar' ? ... : ...` or `l('en', 'ar')`), as they break the other 7 supported languages (`ur`, `fa`, `fr`, `es`, `de`, `tr`, `hi`).
3. **Preserve Interpolation Tokens:** Keep `{name}`, `{count}`, `%s`, and technical acronyms (`POS`, `VAT`, `TRN`, `CR`, `GRN`, `CSV`, `PDF`, `Ctrl+K`) intact during translation.
4. **Run Gate Before Build:** Always run `npm run check:i18n` before `npm run build`.

## 3. Code Style & Stack Constraints
- **Backend:** Python 3.11+, Django 5.2, clean REST JSON endpoints, explicit transaction boundaries (`transaction.atomic()`).
- **Frontend:** Vue 3 `<script setup lang="ts">`, clean CSS variables supporting dark/light themes and logical properties (`margin-inline-start`, `padding-inline-end`) for seamless RTL/LTR mirroring.
