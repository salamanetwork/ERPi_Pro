# ERPi Pro v2.8.1 — تشغيل الإصدار الكامل على WSL2

ضع ERPi_Pro_v2_8_1_Full.zip في مجلد Coding بجوار مجلد الإصدار السابق. أوقف الخادم السابق بـ Ctrl+C وانتظر خروجه. من WSL استخدم نفس الحساب الذي شغّلت به v2.8.0.

```bash
cd /mnt/c/Users/SNW5578/Coding &&
sha256sum -c ERPi_Pro_v2_8_1_Full.zip.sha256 &&
unzip -q ERPi_Pro_v2_8_1_Full.zip &&
cd ERPi_Pro_v2_8_1 &&
ERP_WSL_PORT=8001 bash start_erp_wsl.sh --upgrade
```

المجلد الجديد يحتوي على المشغّل، الشفرة الكاملة في erp-backbone، متطلبات Python وحزمة wheelhouse، وواجهة Vue المجمعة في erp-backbone/frontend/dist. عند التشغيل الافتراضي يستخدم المشغّل نفس مسار التثبيت الداخلي في HOME الذي استخدمته النسخ السابقة. إذا سبق أن استخدمت وسيط installed-project-directory مخصصًا، مرّر **نفس المسار الداخلي** بعد --upgrade. اختيار مجلد الاستخراج الجديد كوسيط سيختار مثيلًا آخر.

في تثبيت قائم تظهر رسالة Saving a pre-upgrade backup ثم Preflight وmigrate. تضيف الترقية جداول إعدادات الترحيل وجدولة الاعتراف بالإيراد وروابط القيود. من Module Manager فعّل Subscriptions بعد Parties وDocuments & Billing وAccounting واعتمادياتها. يحتفظ المشغّل بنسخة التطبيق السابق عند تبديل المجلد الداخلي. تعليمات استعادة النسخ الاحتياطية في DEPLOYMENT_GUIDE_AR.md. متطلبات النظام كما في v2.8.0، ومنها Redis 8 المحلي أو Docker، وPostgreSQL. يشغّل المشغّل Worker وOutbox Dispatcher.

## تفعيل الخدمات

1. افتح `/admin/modules`. راجع الاعتماديات ثم فعّل Publication وCustomer Accounts وما يطلبه النظام من اعتماديات، ثم Parties & Contacts، ثم Scheduling & Appointments، ثم Documents & Billing.
2. في Access Control أضف صلاحيات العرض والإدارة للدور في الموقع المطلوب. للفوترة صلاحيتان منفصلتان للإصدار أو الإلغاء وتسجيل الدفعات. الواجهات من القائمة أو من `/parties` و`/scheduling` و`/billing_core`.
3. اختر الموقع. ترك مساحة النشر فارغة يعرض سجلات الموقع التي أُنشئت دون مساحة. اختيار مساحة يعرض سجلاتها فقط. استخدم نفس الاختيار في المراحل الثلاث للتمرين.
4. افتح `/admin/audit-trail`، وجرّب البحث وتغيير حجم الصفحة بين 10 و25 و50. يحتفظ تصدير CSV بفلاتر السجل على كل الصفحات.
5. راجع `ACCEPTANCE_AR.md` لتمرين الإصدار. يمكن إعادة فتح الواجهة السابقة للنشر على `/p/main-store/`.
