> **مرجع الإصدار الحالي (`v2.11.0` — 2026-11-28):** هذا الدليل التخصصي معتمد ضمن حزمة `v2.11.0` (29 عقد موديول، 9 لغات واجهة وطباعة). لحالة التسليم ونقطة الفحص الحالية راجع `CHECKPOINT_V2_11_0.md` و`ERPi_Pro_Handoff_Docs/`.

# عقد الموديول v0.1

من مجلد erp-backbone وباستخدام Python لبيئة المشروع، يولّد الأمر التالي تطبيقًا حقيقيًا داخل المصدر.

```bash
python manage.py create_erpi_module field_service --name-en "Field Service" --name-ar "الخدمات الميدانية" --scope site
```

النطاق الافتراضي site. ينتج الخيار space ارتباطًا بموقع النشر مع اعتماد publication والتحقق من صلاحية المستخدم في الموقع الداخلي للفرع. يتطلب global صلاحية على All sites. مفتاح السجل عدد صحيح، والعلاقات بالنطاق تستخدم PROTECT. أوامر القراءة والإنشاء تمر من نفس طبقة جلسات الموظفين والصلاحيات المعتمدة. تمنع Idempotency-Key تكرار الإنشاء.

## محتويات التطبيق

| الملف | وظيفته |
|---|---|
| manifest.json | الاسم العربي والإنجليزي، الإصدار، contract_version، category، dependencies، scope_mode، تعريفات الصلاحيات، التنقل والأحداث |
| erpi_module.marker | إشارة اكتشاف للتطبيق المحلي الموثوق عند بدء Django |
| models.py وmigrations/0001_initial.py | الجداول وترحيل قابل للتشغيل على PostgreSQL |
| urls.py وviews.py | GET وPOST على /api/field_service/records، مع تقسيم النتائج إلى صفحات |
| events.py | معالج الحدث. يعالج السجل داخل معاملة واحدة ويحفظ إيصالًا فريدًا لمنع ازدواج الأثر |
| ui/ModulePage.vue | شاشة ثنائية اللغة تُكتشف عند بناء Vue |
| tests.py | اختبارات صلاحيات ونطاق وعزل وOutbox |

## التجربة على قاعدة تطوير

```bash
python manage.py check &&
python manage.py migrate &&
python manage.py test field_service &&
(cd frontend && npm ci && npm run build)
```

أعد تشغيل Django والـ Dispatcher والعامل. عند العمل على نسخة المصدر المستخرجة، استخدم نفس مشغّل WSL مع --upgrade بعد إعادة بناء الواجهة لنشر الملفات في المثيل المثبّت. افتح Module Manager وفعّل الموديول، ثم اربط صلاحيات field_service.view وfield_service.create بدور على النطاق المطلوب. افتح /field_service. أضف التطبيق إلى مصدر إصدارك الدائم قبل أي ترقية تالية. تظل البيانات في PostgreSQL عند تعطيل الموديول.

## القواعد

- platform_core لخدمات المنصة الأساسية. shared_business للموديولات المشتركة، وdomain_module للتوسعات التخصصية.
- يحتفظ permissions بأكواد RBAC القديمة. لكل كود تعريف في permission_definitions يتضمن name_en وname_ar وdescription_en وdescription_ar وaction_type. تنقل المنصة هذه القيم إلى واجهة الإدارة عند التحميل.
- dependencies تعكس required_dependencies للاعتماديات الإلزامية. optional_dependencies تعرّف التكاملات الاختيارية.
- events_published أسماء الأحداث المحلية، مثل created. events_subscribed موضوعات كاملة، مثل field_service.created. سجّل نشر الحدث مع تغيير البيانات داخل transaction.atomic. ينشئ Outbox صف تسليم لكل مشترك مفعّل، في نفس المعاملة.
- يعالج Dispatcher دفعات صغيرة باستخدام SELECT FOR UPDATE SKIP LOCKED. نجاح المعالج وحفظ الإيصال في نفس المعاملة. الفشل يؤجّل المحاولة، والتعطيل يوقف تسليم المهام المعلقة لذلك الموديول حتى إعادة تفعيله. للآثار الخارجية استخدم مفتاح منع تكرار لدى المزود.
