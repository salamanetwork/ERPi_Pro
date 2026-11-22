# ERPi Pro — Master handoff and approved release roadmap

## 1. Current baseline — v2.8.1 Subscription revenue recognition

Subscriptions map six posting accounts per site. Billing Core owns documents and receipts. PostgreSQL Outbox delivery requests idempotent subscription journals. Cumulative daily recognition uses completed service days in the contract timezone, checkpoints a cycle schedule and resolves rounding at period end. Finance administrators review mapping and pending cycles. Historical replay uses backfill_subscription_gl. This release extends the existing module (28 contracts). Workspace billing KPIs continue to measure operational documents.

### Previous baseline — v2.8.0 Subscriptions

The owner accepted v2.7.2 on WSL2 with Dubai Islamic Bank #1 reconciled and both differences zero. v2.8.0 follows that release directly. Subscriptions own site-scoped plans, historical price versions, contracts, cycle reservations, prorations, credit allocations, dunning stages and cancellation requests. Billing Core owns invoice numbering, totals, issuance and payment receipts. In v2.8.0 operational billing did not post revenue or tax to the GL. No marketing consent is inferred from a service contract.

The baseline migration rehearsal enabled the previous modules and upgraded a disposable PostgreSQL 16.2 database with a reconciled statement. Refer to ACCEPTANCE_AR.md for operator checks on the installed instance.

The owner verified the v2.7.1 Employee Expenses directory and multi-line form on WSL2. The v2.7.2 Finance module adds site/account-scoped bank statements, mapped CSV previews, private source attachments, posted-GL candidate ranking, grouped clearing, explicit adjustment journals, audited unmatching and locked statement closure. Statements carry forward the last reconciled balance. Expense settlement references are searchable. Only transactions that produced a bank GL line are eligible. Dashboard variance is the sum of absolute open-statement differences for the selected period and site.

Internal record discussions remain on the roadmap; release numbering is open.

The platform integrates purchasing, inventory, sales, accounting, budgets, HR/payroll, attendance, maintenance, CRM, supply chain, manufacturing, projects, helpdesk, marketing, publication, external accounts, private media, parties, scheduling and billing. v2.6.4 linked parties to operational records; v2.6.5 added training, security health and customer folders; v2.6.6 introduced signed webhooks, connector registry, privacy operations and module audits.

### v2.7.0 changes

Personal menu preferences are scoped to the authenticated staff account. The server stores group order, child order and hidden IDs under optimistic concurrency. The sidebar filters the permitted catalog before applying preferences. Browser-local storage handles expansion, cached preferences and per-user, per-guide checklist progress. Settings → Navigation continues to control global visibility. Reset UI and checklist resets are independent.

Help uses bilingual guide and step identifiers. Gateway fields have contextual explanations; the security dashboard explains environment-specific warnings. Outbound logging uses explicitly declared route templates. WSL keeps a distinct MFA key and transactionally rewraps enrolled secrets at upgrade. Preserve the key files with protected backups; existing verified sessions may request a fresh MFA login. Existing 0.1 manifests remain readable. New scaffolded modules use contract 1.0. Compatibility ranges on the shipped apps were extended for the 2.7 release line.

Release gates: 250 Django tests on PostgreSQL 16.2 (228 inherited, six bank CSV boundaries, one delegation test, fifteen subscription tests), 28/28 contracts, no migration drift, preflight passed and Vue production bundle built. Acceptance includes simultaneous PostgreSQL workers issuing exactly one invoice, the unsent-final-notice suspension guard, broker unavailability/redelivery, public customer ownership checks and generation of both PDF locales. The automated outage checks inject broker unavailability; physical WSL2/Redis 8/Mailpit and browser review remain local acceptance steps.

## 2. Non-Negotiable Architectural & Packaging Rules

1. **Modular Monolith Boundaries:** All business modules live as modular Django apps inside `erp-backbone/` and register via **Module Contract v1.0** for new modules (legacy v0.1 supported) (`manifest.json` + `erpi_module.marker`).
2. **Database Integrity:**
   - Use **integer Primary Keys** (`BigAutoField` / `AutoField`).
   - Use **`on_delete=models.PROTECT`** on all cross-module foreign keys (`Site`, `PublicationSpace`, `Party`, `User`, etc.).
   - Use optimistic concurrency (`revision` integer field) and idempotency keys where applicable.
3. **Strict Security Isolation (`backbone/production.py`):**
   - Never merge internal employee session auth (`SECRET_KEY`) with external customer JWTs (`ERP_CUSTOMER_JWT_KEY`) or MFA encryption (`ERP_MFA_KEY`).
   - External customer tokens (`Authorization: Bearer ...`) must remain strictly blocked from internal/admin endpoints.
4. **Full Standalone Release Packaging (MANDATORY):**
   - **Never** deliver incremental patch ZIPs that clutter existing folders.
   - Always deliver a **complete, self-contained standalone archive** (`ERPi_Pro_vX_Y_Z_Full.zip`) extracting to a clean root folder (`ERPi_Pro_vX_Y_Z/`) with `frontend/dist` pre-built and `start_erp_wsl.sh` ready for `--upgrade`.
5. **Bilingual & Self-Documenting Standard:**
   - Every screen, permission, error message, and workflow guide must support **English (LTR)** and **Arabic (RTL)**.

---

## 3. Unified Entity & Access Map (System Mental Model)

| Entity | Model / App | Purpose | Login Access | Relationship Rules |
| :--- | :--- | :--- | :--- | :--- |
| **Internal User** | `auth.User` + `backbone.Profile` | Staff/Admin operating the ERP | `/login` → `/workspace` (Session + MFA) | Scoped to `Site` via `Role`. |
| **Employee** | `hr.Employee` | HR, attendance, payroll record | None directly (unless linked to `User`) | Optional `OneToOne` link to `auth.User`. |
| **Public Site** | `publication.PublicationSpace` | External public website (e.g., `Main Store`) | Publicly viewable at `/p/<site_code>/` | Belongs to an internal `backbone.Site` (`PROTECT`). |
| **Customer Account** | `public_identity.ExternalIdentity` | External customer login on a Public Site | `/public/account` (Isolated JWT) | Scoped to `PublicationSpace`; zero access to internal ERP. |
| **Commercial Party** | `parties.Party` | Unified commercial contact (Customer/Supplier) | None directly | Domain-agnostic anchor for `Appointment` and `Invoice`. |
| **Legacy Customer / Supplier** | `sales.Customer`, `purchasing.Supplier` | Legacy v0.1 sales/purchasing records | Internal records only | Nullable bridge to parties.Party since v2.6.4. |

---

## 4. Approved core delivery sequence

| Release | Scope | Acceptance gate |
|---|---|---|
| v2.7.1 | Employee Expenses: receipts, manager approval, accounting and reimbursement | Paying a posted claim reduces employee liability exactly once |
| v2.7.2 | Bank Reconciliation: statement import, manual match, audited undo | Repeat import creates no duplicate; matching a settled payment adds no duplicate GL posting |
| v2.8.0 | Subscriptions: parties, recurring billing, renewal reminders | One invoice per contract period, including retries |
| v2.8.1 | Subscription GL integration and recognition | Requires billing_core and accounting |
| Planned | Internal record discussions and mentions | Every read respects the source document scope; mentions grant no access |
| v2.9.0 | Retail POS: cashier shift, cash/card recording, stock and settlement | Concurrent sale of the last unit, shift totals and GL agree |
| v2.9.1 | Recruitment: applicant pipeline and hiring | Repeating conversion creates one employee |
| v2.9.2 | Appraisals: employee and reviewer workflow | Confidential review access and audited final grades |
| v2.9.3 | Quality Control: inbound and production inspections | Quarantined stock excluded from available stock until released |

Review and approve each business release independently. Industry verticals follow the shared core program. Employee Expenses and Bank Reconciliation are accepted baselines. The next acceptance is the v2.8.1 financial integration.

## Security and event contracts

**Delegation.** One direct role assignment authorizes one grantor-to-beneficiary hop. Authorization checks the source assignment owner, role, live permission set, site and validity window. A beneficiary's delegated rights cannot be used as a source assignment. An extension preserves the beneficiary, role, site and permission list and requires approval. Deleting the source assignment invalidates the grants and marks current/pending delegations revoked. See backbone/access.py, delegations.py and test_delegations.py.

**Webhook ingress.** POST /api/webhooks/v1/{slug}/ with a body of at most 512 KiB. By default send X-Webhook-Timestamp (ASCII Unix seconds) and X-Webhook-Signature (lowercase HMAC-SHA256 hex, optional literal sha256= prefix). Sign the exact timestamp bytes, a literal dot and the unmodified request-body bytes using the endpoint signing secret. Header names are endpoint settings. The receiver compares in constant time and applies an absolute time window (default 300 seconds). With no configured timestamp header it signs the raw bytes only and has no time-based replay cutoff. Duplicate protection is scoped to endpoint and X-Webhook-Id, then X-Event-Id, then SHA-256 of raw bytes. Resends should reuse the same delivery ID. Rotating an endpoint signing secret in Integrations & Webhooks returns the new secret once. Coordinate cutover with the sending service.

**Key maintenance.** Stop application traffic, dispatcher and workers, take a DB backup, retain the old protected key material with the backup, and verify a separate restore. Set four independent high-entropy values in the deployment secret store. Avoid literal keys in the shell history, logs, tickets or release archive.

| Key | Change procedure | Verification |
|---|---|---|
| ERP_SECRET_KEY (Django SECRET_KEY) | Revoke staff sessions via Security Health, update the application environment and WSL django.key if applicable, restart all processes. Check dependencies using Django signing before scheduling cutover. | Fresh staff login and MFA work. Existing signed sessions and outstanding signed links may need renewal. |
| ERP_CUSTOMER_JWT_KEY | Configure an explicit key across web and worker environments. Revoke external sessions via Security Health, replace signing key and restart services. WSL development otherwise derives its fallback from Django's secret. | An old bearer/refresh fails and a fresh customer login works. |
| ERP_MFA_KEY | Set the new key, retain the old key privately, and run rotate_security_keys with --old-mfa-key in a restricted maintenance shell. Match the persisted WSL mfa.key to the new value. | Existing enrolled authenticator completes an MFA challenge. |
| ERP_CONNECTOR_KEY | Set the new key and pass --old-connector-key to the same command. The command rewraps connector credentials and endpoint signing secrets. Match WSL connector.key. | Test Connection and a signed test delivery both succeed with existing provider credentials. |

From the configured Django environment run `python manage.py rotate_security_keys --old-mfa-key "$PREVIOUS_MFA_KEY" --old-connector-key "$PREVIOUS_CONNECTOR_KEY"`. Only supply flags for keys being replaced. Restrict OS process inspection during maintenance because the command accepts old keys via argv. Both rewrap operations share a database transaction. On error restore the original service environment before reopening traffic; on success run audit_modules and verify the checks above. The command does not rotate Django sessions or customer JWTs. A rollback after successful re-encryption requires matched DB and key material.

**Event boundaries.** Cross-module writes that must commit together call the owner's service synchronously. The subscriptions worker locks the contract, reserves the unique cycle, applies credit, calls billing_core.services, and commits the invoice and domain events together. OutboxEvent is the PostgreSQL record of asynchronous intent. Dispatcher delivery and invoice-triggered contract wakeups tolerate redelivery. SMTP acknowledgement and database commit are separate operations; a crash in between can repeat an email, with a stable Message-ID per notice. Mailpit is the local SMTP destination. Collection notices use the party's service email and do not subscribe the party to Marketing.

### Subscription operators and integration points

Staff reads and writes are under /api/subscriptions. Collections are plans, versions, contracts, cycles, policy, notices and requests. Requests carry site_id and optional space_id. Plan and contract mutations require an Idempotency-Key; updates also carry revision. The trial anchors the first chargeable date. Months follow the original day of month, clamped to month end, preserving an original end-of-month anchor. Suspended periods are skipped when the service resumes. Recurring cycles and receipts use the active ledger currency snapshot. Calendar-cycle and tax-rate changes take effect at renewal. Proration entries retain their source tax rate. Downgrade credit includes the source-rate tax rounded on that adjustment. Apply its monetary value after current-cycle tax; billing_core invoices expose applied_credit and total = subtotal + tax − applied_credit. Existing invoices migrate with zero credit. Use billing-preview to inspect the next period and applied credit. The four dashboard cards report current operational contract counts and issued document balances.

Customer GET /api/customer/v1/subscriptions requires a verified external bearer and current membership. POST /api/customer/v1/subscriptions/{id}/cancellation-requests additionally requires CSRF, a UUID idempotency key, reason and contract revision. PDF downloads use /api/customer/v1/business/{invoices|receipts}/{id}/download?lang=ar or en. The existing HTML /print route remains available. PDFs consist of paginated rendered pages using the bundled DejaVu Sans font and Pillow with Arabic shaping; the HTML route is available for selectable text and assistive technology.

In development the scheduler and Celery worker use the existing notifications queue. PostgreSQL retains the contract, cycle and notice state throughout outages. The dispatcher leases work in bounded batches; duplicate tasks recheck the contract revision and unique billing period. A successful SMTP submission means acceptance by the local relay, not confirmed delivery to a customer's inbox. billing_core payment and void events wake subscription checks through module_events. A suspended contract requires staff review to resume.
