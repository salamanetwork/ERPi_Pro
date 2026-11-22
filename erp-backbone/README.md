# ERP Backbone 0.5 — local acceptance

هذا التسليم يضيف التفويض المؤقت بصلاحيات ونطاق وفترة محددة مع اعتماد الأدمن. تتضمن المنصة التحقق بخطوتين والنسخ الاحتياطي والاستعادة. يبدأ النظام بموديولز الأعمال معطّلة. يتضمن تسجيل الدخول المحلي، ومدير الموديولز، والمستخدمين والأدوار ونطاق الموقع، وسجل تدقيق أساسيًا، والأصول وأوامر الشغل. يدعم أمر الشغل المسودة والإرسال وربط أصل من الموقع نفسه عند تفعيل موديول الأصول.

## التشغيل على Fedora أو Linux

الحزمة تتضمن واجهة Vue مبنية وملفات Python للتثبيت المحلي على **Linux x86-64 مع Python 3.11 أو 3.12**. يلزم وجود Python 3.12 مع pip وPostgreSQL 14 أو أحدث على الجهاز أو الشبكة مسبقًا. استخدم قاعدة اختبار فارغة ومستخدم PostgreSQL يملكها. استخدم قاعدة مستقلة لكل تثبيت. يلزم لهذا التشغيل التجريبي الاتصال بقاعدة البيانات المحلية فقط.

افتح طرفية في مجلد المشروع ونفّذ:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --no-index --find-links wheelhouse --require-hashes -r requirements.txt

export ERP_DEBUG=1
export ERP_SECRET_KEY="$(python -c 'from secrets import token_urlsafe; print(token_urlsafe(48))')"
export ERP_DB_NAME=erp_backbone
export ERP_DB_USER=erp_backbone
export ERP_DB_HOST=127.0.0.1
export ERP_DB_PORT=5432
read -rsp "PostgreSQL password: " ERP_DB_PASSWORD
export ERP_DB_PASSWORD
printf '\n'

python manage.py migrate
python manage.py setup_backbone
python manage.py runserver 127.0.0.1:8000
```

افتح [http://127.0.0.1:8000](http://127.0.0.1:8000) وسجّل باسم `admin` وكلمة السر التي اخترتها أثناء الإعداد. شغّل `setup_backbone` مرة واحدة لكل تثبيت. احتفظ بمفتاح `ERP_SECRET_KEY` في إعداد محلي محمي خاص بالتثبيت إذا أردت بقاء الجلسات بعد إعادة التشغيل. استخدم مفتاحًا مستقلًا لكل تثبيت.

هذا التشغيل التجريبي يستمع على جهازك فقط. قبل تشغيله لبيانات شركة فعلية، يلزم نشر خادم إنتاج عبر HTTPS واختبار القبول على شبكة الشركة وأجهزتها.

## خطوات التجربة

1. افتح **Module manager** وتأكد إن الاتنين معطّلين. فعّل **Work orders** لوحده، وأنشئ مسودة. حقل ربط الأصل يظهر عند تفعيل **Assets** وإتاحة صلاحية العرض.
2. عطّل **Work orders** وفعّل **Assets** لوحده، وأضف أصلًا. عند الربط لاحقًا اختر نفس الموقع في الأصل والأمر، أو اتركهما بدون موقع.
3. فعّل الاثنين وأنشئ أمرًا مرتبطًا بالأصل. أرسل الأمر، ثم عطّل **Assets**. ارجع للأمر وتحقق من بقاء مرجع الأصل التاريخي.
4. من **Access control** أنشئ موقعين ومستخدمًا، ثم عيّن له **Work order operator** في موقع و**Reader** في الموقع الثاني. جرّب حسابه على الأمرين. ويمكن إنشاء دور مخصص أو نسخه من دور موجود.
5. من **Settings** اختر العربية أو غيّر الثيم واضغط حفظ. أعد فتح الصفحة للتأكد من بقاء الإعدادات.

## التطوير

ملفات `backbone/` تدير الحسابات والصلاحيات والواجهة المشتركة، و`assets/` و`workorders/` يملكان قواعدهما وبياناتهما. تعريف كل موديول في `manifest.json` داخل مجلده. يوضح [openapi.yaml](openapi.yaml) عقد مسارات تجربة الأصول وأوامر الشغل. ملفات الواجهة الجاهزة تُخدم من نفس عنوان التطبيق.

لإعادة بناء الواجهة من المصدر، استخدم Node 22.12 أو أحدث ضمن سلسلة متوافقة مع Vite 7 وnpm، مع تنزيل الاعتماديات مرة عند التجهيز:

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

شغّل اختبارات السيرفر على قاعدة تطوير بحساب PostgreSQL يسمح بإنشاء قاعدة الاختبارات:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

## نطاق التسليم

حزمة التجربة تشمل الكود والواجهة المبنية وملفات Python المحلية. استعادة السيرفر تلقائيًا، وبوابة المالك، والتفويض، وحزم الموديولز الخارجية والتحديثات، وتصدير الشركة، وقواعد توقيت يوم التشغيل لها تسليماتها اللاحقة. تدخل أوامر الشغل الجديدة دورة الموافقة بعد نشر أول قالب من شاشة Approval templates.

إشعارات تراخيص ملفات الواجهة المبنية موجودة في `third_party_notices/`.

يدعم موديول طلبات الشراء المعطّل افتراضيًا قالب موافقات لكل عملة. يُثبّت القالب عند الإرسال، ويضيف خطوة المالية عندما يتجاوز مجموع البنود المقربة الحد المحدد. راجع دليل التشغيل في الحزمة لتجربة طلبين.

## v0.6.0 document services

The document registry currently exposes purchase requisitions. Numbering is allocated
in the same database transaction as document creation and supports company-wide and
site-scoped sequences. Existing requisition numbers are preserved. Draft archive
and restore use a revision check. Attachments are private, stored below the configured
media root and included in backup and restore. Print layouts render an A4 page in
Arabic or English for the browser's Print and Save as PDF commands.

Inbox is initially empty. Integrators register a document and its current-step
authorization callback, then call `open_task` and `close_task` in the transaction
that changes the approval step. Existing PR and work order review queues continue
through their existing routes.

Run the acceptance tests on a disposable UTF-8 PostgreSQL database with
`python manage.py test`. See the separate Arabic acceptance and accounting design
deliverables.

## v0.6.1 purchase orders

The purchasing module converts an approved requisition into one purchase order.
Draft price changes require a reason and retain a line-by-line price history.
Issue freezes the document, supplier and line data. Void keeps the snapshot and
source requisition link. The PO number is allocated at conversion from a
company-wide sequence.

A future receiving workflow must call `purchasing.orders.register_receipt`
inside the transaction confirming its receipt, before writing stock movements.
That function and Void lock the same PO row. A confirmed receipt link remains
as a lifecycle reference.

## v0.7.0 inventory and receiving

Enable Inventory after Purchasing. The inventory ledger stores immutable stock
movements with signed quantities and values, source IDs, costing policy, and
business dates. Moving average is maintained per item and site, and quantities
are tracked per location. Goods receipts use the issued AED order snapshot and
pin the item, purchase unit, and conversion on first confirmation. Receipt line
amounts use cumulative rounding so split receipts add up to the issued amount.
Supplier returns reopen the order balance and use current moving average while
retaining the historical receipt value. Receipt corrections use historical value
subject to quantity and value consistency checks.

Opening entries are unique per item/location and can be entered in batches before
site cutover. Explicit close or first confirmed receipt closes the opening period.
Confirmation takes the PO lock shared with Void, then a site lock shared with
other stock mutations. Movement facts identify accounting event types for later
integration. Ledger postings and foreign-exchange conversion are deferred.

## v0.8.0 accounting

Enable Accounting after Inventory. The seeded AED ledger needs a financial
period and an explicitly reviewed cutover. Movement IDs are the idempotency keys
for journal posting. Cutover carries pre-cutover stock value and per-receipt
open accruals. Inventory movements, posting results, and journal entries commit
atomically. Pending accounting exceptions can be retried without repeating stock.
Accounting permissions are granted at company scope.

## v0.9.0 sales basics

Sales uses the existing inventory site lock, valuation, ledger and posting
queue. Delivery recognizes revenue, unbilled sales, COGS and stock. Invoices
transfer available amounts from unbilled sales to AR. Payment allocations and
credit notes reduce AR. Physical returns restore the historical delivery cost.
Sales events carry dependencies and have a unique posting result. Sales invoice
PDFs are operational AED documents without VAT calculation.
