# PRODUCT_ROADMAP.md — ERPi Pro


## 0. خط الأساس الحالي — `v2.11.0` (2026-11-28)
- **الإصدار المعتمد:** `v2.11.0` (29 عقد وموديول متوافقين بالكامل عبر `manage.py audit_modules`).
- **الطباعة متعددة اللغات وهوية الشركة:** دعم طباعة كافة المستندات باللغات التسع (`en, ar, ur, fa, fr, es, de, tr, hi`) مع اتجاه `RTL`/`LTR` تلقائي (`backbone/print_i18n.py`)، وإضافة الرقم الضريبي (TRN) والسجل التجاري (CR) والعملة الافتراضية والعنوان في `CompanyProfile`.
- **حالة الترجمة والواجهة:** 1,988 مفتاح ترجمة متطابق، 77 موضوع مساعدة، و259 شرح حقل، مع إصلاح تنبيهات الواجهة (`useUnifiedNotice.ts`).
- **التفاصيل الكاملة:** راجع `CHECKPOINT_V2_11_0.md` ومجلد `ERPi_Pro_Handoff_Docs/`.

---

خط الأساس الحالي v2.8.0. اكتمل تنفيذ الاشتراكات والفوترة الدورية بعد قبول التسوية البنكية v2.7.2. الترتيب التنفيذي المعتمد في erpi_pro_master_handoff_roadmap.md. الخطوة التالية بعد القبول المحلي هي v2.8.1 للمحادثات الداخلية.

## 1. المشروع والهوية

- **الاسم:** ERPi Pro (Enterprise Resource Planning Intelligent Professional).
- **المعمارية:** Django + PostgreSQL + Vue 3 + TypeScript، Loose Coupling، Django Apps مستقلة، Outbox + Celery + Redis للعمليات غير المتزامنة.
- **الالتزام:** دليل المعمارية `ARCHITECTURE_GUIDE.md`، وكل إصدار له اختبارات قبول موثقة.
- **الجمهور المستهدف:** شركات متوسطة وكبيرة، مع دعم Multi-Site و Multi-Currency (مستقبلاً).

## 2. ما تم تسليمه واعتماده (Confirmed)

- **Core Backbone (v0.1 → v1.0):** Assets, Work Orders, Approvals, Delegation, PR, PO, Inventory, Accounting, Sales, HR & Payroll، Inbox, Global Search, Saved Views, CSV Import/Export.
- **UI/UX & Master Data (v1.7.x):** Sidebar Grouping، Module Manager، User Profile، HR Setup (Departments, Job Titles, Grades)، Offboarding.
- **Budgeting (v1.4):** Cost Centers، Budgets، Budget vs Actual، Versions.
- **Dashboard & Analytics (v1.3 / v1.8.9):** KPIs، Charts (ECharts)، AP/AR Aging، GRNI، PM On-Time.
- **Manufacturing / MRP (v2.2.x):** BOM (Multi-Level)، Manufacturing Orders، WIP، MRP Runs، Disposal/Scrap.
- **Supply Chain (v2.1.x):** Stock Transfers، Reservations، Shipment Tracking، In-Transit Accounting.
- **CRM (v2.0.x):** Leads، Opportunities، Kanban، Stage History، Quotation Linking، Customer Request.
- **Project Management (v2.3.x):** Projects، Tasks، Kanban، Timesheets، Budget Baseline، Labour Rates.
- **Services / Helpdesk (v2.4.x):** Tickets، Categories، SLA Policies، Internal Notes، Linking to WO/Project، Outbox+Redis+Redis Failover، Module Compatibility Preflight.

## 3. الموديولات المستهدفة (Future Scope)

### 3.1 Finance — المالية والحسابات
- Accounting (✅ Done)
- Invoicing (Customer + Supplier) (✅ Partial, needs AR/AP full cycle polish)
- Expenses (تتبع واعتماد مصروفات الموظفين)
- E-Signature (توثيق العقود رقمياً)
- Spreadsheet (جداول بيانات حية تشبه Excel)
- ESG / Sustainability (تتبع مؤشرات البيئة والحوكمة)

### 3.2 Sales — المبيعات والعملاء
- CRM (✅ Done)
- Sales (✅ Done)
- Subscriptions (تم في v2.8.0 — اشتراكات متكررة وفواتير دورية)
- POS Shop (نقاط بيع للتجزئة، باركود، Offline)
- POS Restaurant (صالات، طاولات، تقسيم فواتير)
- Rental (تأجير أصول ومعدات)

### 3.3 Websites — المواقع والتجارة الإلكترونية
- Website Builder (Drag & Drop، SEO)
- eCommerce (متجر مرتبط بالمخزون)
- Events (حجز تذاكر، باركود حضور)
- Blog (مقالات وتعليقات)
- eLearning (دورات واختبارات)
- Forum (منتدى مجتمعي)

### 3.4 Supply Chain & Manufacturing
- Inventory (✅ Done)
- Manufacturing / MRP (✅ Done)
- Maintenance (✅ Done)
- PLM (Product Lifecycle Management — إصدارات التصميم)
- Quality (نقاط فحص، قبول/رفض، شهادات)
- Purchase (✅ Done)

### 3.5 Human Resources
- Employees (✅ Done)
- Payroll (✅ Done)
- Recruitment (توظيف، مقابلات، عروض)
- Time Off (✅ Done)
- Appraisals (KPIs وتقييم أداء)
- Fleet (أساطيل، وقود، تأمين، مخالفات)

### 3.6 Marketing (v2.5 — القادم)
- Social Marketing (جدولة منشورات)
- Email Marketing (حملات جماعية، قياس فتح)
- SMS Marketing (رسائل عروض)
- Marketing Automation (مسارات تفاعل آلية)
- WhatsApp Integration (إرسال فواتير وتنبيهات)
- Surveys (استبيانات رضا)

### 3.7 Services
- Projects (✅ Done)
- Timesheets (✅ Done)
- Planning (جدولة موارد، منع تداخل)
- Appointments (حجز مواعيد أونلاين)
- Live Chat (دعم مباشر)
- Helpdesk (✅ Done)

### 3.8 Productivity
- AI Assistant (استعلامات باللغة الطبيعية)
- Discuss (مراسلة داخلية)
- VoIP (ربط السنترال بملفات العملاء)
- IoT (قراءة حساسات ومعدات)
- Documents (أرشيف مركزي)
- Knowledge Base (موسوعة داخلية)

## 4. التوسعات التخصصية المستقبلية (v3.x)

- **Healthcare:** مستشفيات، عيادات، عيادات أسنان، صيدليات، روشتات.
- **Labs:** معامل تحاليل، وحدات صحية.
- **IT Service Desk:** إدارة IT، شبكات، CCTV.
- **Engineering:** إدارة هندسية بمشتملاتها.

## 5. الأفكار الاستراتيجية المعتمدة (من Gemini)

### 5.1 المعمارية والأمان والحوكمة
- **WORM Audit Storage:** سجل تدقيق مشفر لا يقبل التعديل (S3 Object Lock أو حل خارجي).
- **Field-Level & Row-Level Security:** صلاحيات على مستوى الحقول والصفوف.
- **Dynamic Feature Flags & Licensing:** تراخيص مرنة حسب العميل.
- **Centralized Audit Trail:** تسجيل بصمة الجهاز لكل حركة.
- **Plugin Architecture:** Database isolation، Foreign Keys آمنة.

### 5.2 الذكاء الاصطناعي والأتمتة
- **AI Assistant:** استعلامات باللغة الطبيعية، تقارير ذكية.
- **Visual Workflow Automation (If-This-Then-That):** بناء قواعد عمل آلية.
- **AI Anomaly Detection:** اكتشاف الأخطاء المحاسبية والحالات الشاذة.

### 5.3 التشغيل الميداني وتجربة المستخدم
- **Offline-First Sync:** للمستودعات والميدان (PWA + IndexedDB).
- **Global Command Palette (Ctrl+K):** بحث وتنقل سريع.
- **Actionable Alerts:** إشعارات قابلة للتنفيذ.
- **Multi-Channel Notifications:** WhatsApp, SMS, Email, Telegram.
- **Dark Mode & Adaptive UI.**

### 5.4 الإدارة المالية والتشغيلية المتقدمة
- **Multi-Currency with Live Rates:** يدعم أسعار صرف حية وفروق العملات (تغيير جذري، يحتاج تصميم Schema).
- **Cash Flow Forecasting:** تخطيط وتنبؤ بالسيولة.
- **Fixed Asset & Depreciation Engine:** إهلاكات تلقائية.
- **Contract Lifecycle Management (CLM):** إدارة عقود وتجديد تلقائي.
- **Budget Control & Variance Engine:** (✅ Done).
- **Fleet Management:** أساطيل، وقود، خطوط سير.

### 5.5 التكامل والربط الخارجي
- **Universal Webhook & API Integration Hub:** مع آليات Retry.
- **Self-Service Portal:** بوابة العملاء والموردين (فواتير، أوامر شراء).

## 6. أولويات التنفيذ — تحديث معتمد في 17 نوفمبر 2026

هذا الترتيب يحل محل أرقام الإصدارات السابقة. لا تُحذف الأفكار المؤجلة، ولا تعتبر أي نسخة مقبولة قبل قبول أحمد المحلي الصريح.

| الإصدار | النطاق | الحالة |
|---|---|---|
| v2.5.1 | Marketing وأزرار الحملة | سُلّمت؛ اختبار القبول المحلي لم يُؤكد بعد. |
| v2.6.0 | PublicationSpace + ExternalIdentity/Session + MediaAsset محلي | تنفيذ المرحلة الأولى المعتمد. |
| v2.6.1 | Content + Moderation + Public API + SEO | مخطط، لم ينفذ. |
| v2.6.2 | Search Service + Manual/Mock Payments | مخطط، لم ينفذ. |
| v2.6.3 | Channels + Redis + قبول تكاملي | مخطط، لم ينفذ. |
| v2.7 | E-commerce | بعد قبول Platform Services. |
| v2.8 | Customer / Supplier Portal | مخطط. |
| v2.9 | Blog | مخطط. |
| v3.0 | Forums | مخطط. |
| v3.1 | Social Network | مخطط. |

### Backlog محفوظ — مؤجل وليس ملغيًا

- **Finance Polish:** تحسين دورة AR/AP، Expenses وE-Sign؛ الرقم القديم v2.6 أصبح ملغى، ويعاد جدولة التنفيذ لاحقًا.
- **HR Polish:** Recruitment وAppraisals وFleet؛ الرقم القديم v2.7 أصبح ملغى، ويعاد جدولة التنفيذ لاحقًا.
- Services Polish: Planning وAppointments وLive Chat وKnowledge.
- Productivity: Discuss وDocuments وKnowledge Base وCommand Palette.
- AI Assistant وVisual Workflow وAnomaly Detection.
- **Multi-Currency:** لم ينفذ، ويحتاج تصميم Schema مستقلًا. الرقم القديم v3.1 لم يعد صالحًا بعد تخصيصه لـ Social Network؛ لا رقم جديد معتمد بعد.
- Offline-First وMobile، والتوسعات Healthcare/Labs/IT/Engineering: تبقى في الرؤية، وأرقام تنفيذها يعاد تقييمها.

### اختيارات الأساس المجانية

SimpleJWT، PostgreSQL FTS، Django FileSystemStorage مع محولات django-storages لاحقًا، Channels/Redis 8 تحت AGPLv3، وManual/Mock Payment Providers. لا اشتراك أو خدمة سحابية إلزامية. Valkey بديل مستقبلي بعد اختبار التوافق، وليس تغييرًا منفذًا.

## 7. حدود التصميم (Non-Goals حتى الآن)

- Work Centers & Scheduling في التصنيع.
- Multi-Currency (مؤجل؛ أعيدت جدولة رقمه ولم يبدأ التنفيذ).
- Offline-First (مؤجل؛ موعد التنفيذ يعاد تقييمه).
- Work Center Costing & Overhead Allocation.
- E-invoicing (حسب بلد العميل).
- Payroll Country-Specific Rules (GOSI, WPS, إلخ).
- Advanced POS / Restaurant.

## 8. القواعد الثابتة

- كل إصدار يلتزم بـ `ARCHITECTURE_GUIDE.md`.
- كل موديول له جداوله و manifest و `platform_compatibility`.
- الـ API يرجع `field_errors` و `field_error_codes` (زي ما هو معمول في v1.8.8).
- استخدام `Idempotency-Key` + `revision` + `SELECT FOR UPDATE` في العمليات الحساسة.
- Outbox Pattern للعمليات غير المتزامنة (Notifications, Emails, Integrations).
- اختبارات قبول مكتوبة قبل التسليم، مع الأرقام المتوقعة.
- الترقية بين الإصدارات لازم تكون مختبرة على PostgreSQL + Backup.

## 9. ملاحظات للـ Context

- بيئة التطوير: WSL2 + PostgreSQL + Redis + Celery Worker + Outbox Dispatcher (كلهم بيبدأوا مع `start_erp_wsl.sh`).
- بيئة الإنتاج: Docker Compose + Nginx + HTTPS (مُختبرة في v1.6).
- النسخ الاحتياطي: Restic + S3 (مُختبر محلياً).
- الـ Core Version الحالي: 0.1 (platform_compatibility).

---
