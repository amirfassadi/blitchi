# منابع فنی و سیاست نسخه‌ها

تاریخ بررسی: ۲۰۲۶-۱۰-۲۷. این پیوندها دلیل ویژگی‌های فنی و پشتیبانی را فراهم می‌کنند؛ انتخاب stack تصمیم پروژه است، نه توصیهٔ قطعی ناشی از خود منابع.

| موضوع | منبع رسمی | کاربرد |
| --- | --- | --- |
| PostgreSQL و پشتیبانی | https://www.postgresql.org/support/versioning/ | انتخاب شاخهٔ 18 پشتیبانی‌شده |
| قفل دیتابیس | https://www.postgresql.org/docs/18/explicit-locking.html | طراحی رقابت صندلی و تراکنش |
| Django LTS | https://www.djangoproject.com/download/ | سیاست ارتقا و پایان پشتیبانی |
| Python با Django | https://docs.djangoproject.com/en/5.2/releases/5.2/ | سازگاری 3.13 و 5.2 |
| DRF | https://www.django-rest-framework.org/community/3.16-announcement/ | سازگاری اولیهٔ پشته؛ نسخهٔ دقیق هنگام bootstrap کنترل شود |
| تراکنش Django | https://docs.djangoproject.com/en/5.2/topics/db/transactions/ | atomic و مرز commit |
| CSRF | https://docs.djangoproject.com/en/5.2/ref/csrf/ | حفاظت فرمان‌های مرورگر و ورود |
| Next.js مستقل | https://nextjs.org/docs/app/guides/self-hosting | استقرار روی سرور خودمان با پراکسی |
| پشتیبانی Next.js | https://nextjs.org/support-policy | انتخاب شاخهٔ پشتیبانی‌شده |
| Node.js | https://github.com/nodejs/node/releases | Node 24 LTS برای runtime پیشنهادی وب |
| Celery پایدار | https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html | broker و worker |
| پیام‌رسانی | https://www.rabbitmq.com/docs/reliability | ack، confirm و رسیدگی به تکرار |
| Docker | https://docs.docker.com/engine/install/ubuntu/ | محیط Ubuntu مرجع |

## کنترل در آغاز ساخت

- دوباره وضعیت امنیت و پشتیبانی منابع بررسی شود.
- نسخهٔ دقیق سازگار Python/Django/DRF و Node/Next/React در lock ثبت شود.
- نسخهٔ پایدار RabbitMQ سازگار با Celery انتخاب و restart و redelivery آزموده شود.
- نسخهٔ patch جدیدتر به‌صورت آزموده ارتقا داده شود؛ این سند فایل نصب نیست.
- Docker image با digest ثابت و صورت مواد نرم‌افزاری و مجوز وابستگی‌ها ثبت شود.
- نسخهٔ آزمایشی، canary و release candidate در مبنای عملیاتی استفاده نشود.

## منابع تکمیلی بازبینی

- [زمان PostgreSQL](https://www.postgresql.org/docs/18/functions-datetime.html): تفاوت now و clock_timestamp.
- [SELECT و SKIP LOCKED](https://www.postgresql.org/docs/18/sql-select.html): گزینهٔ صف کار، نه انتخاب صندلی ناقص.
- [قفل و deadlock](https://www.postgresql.org/docs/18/explicit-locking.html): ترتیب مشترک و retry.

رفتار درگاه محلی و دسترسی registry نیازمند مدرک provider و میزبان واقعی است؛ این‌ها از روی منابع عمومیِ نامرتبط نتیجه‌گیری نشده‌اند.
