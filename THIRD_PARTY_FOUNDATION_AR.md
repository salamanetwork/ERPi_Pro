# تراخيص واعتمادات Platform Services v2.6.0

| المكوّن | الإصدار | الترخيص |
|---|---|---|
| Django REST Framework | 3.16.1 | BSD-3-Clause |
| djangorestframework-simplejwt | 5.5.1 | MIT |
| django-storages | 1.14.6 | BSD-3-Clause |
| PyJWT | 2.15.1 | MIT |
| Redis Docker | 8.2.11-alpine | اختيار AGPLv3 من التراخيص المتاحة |

Python wheels الأصلية مرفقة، وتحتوي تراخيصها. نسخ التراخيص الجديدة في erp-backbone/third_party_notices. الحزمة لا تعيد توزيع Redis binary ولا تعدله؛ تستخدم الصورة الرسمية في Docker. عند نشر نسخة معدلة من Redis راجع التزامات توزيع المصدر للرخصة المختارة.

المصادر:
- https://redis.io/legal/licenses/
- https://github.com/redis/redis/tree/8.2.11
- https://github.com/redis/redis/blob/8.2.11/LICENSE.txt
- https://github.com/docker-library/official-images/blob/master/library/redis
- https://www.gnu.org/licenses/agpl-3.0.html

التشغيل لا يحتاج اشتراكًا. تجهيز الجهاز/الخادم واسم النطاق إن استُخدم وإرسال SMTP خارجي تكاليف تشغيل منفصلة. لا حاجة لخدمة سحابية في تجربة WSL/Mailpit المحلية.
