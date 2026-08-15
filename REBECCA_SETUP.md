# اتصال Rebecca به Angel VPN

ربات از REST API پنل Rebecca استفاده می‌کند. طبق مستندات رسمی Rebecca، API روی `/api` ارائه می‌شود و Swagger/OpenAPI با `DOCS=True` روی `/docs` در دسترس است.

در `.env`:

```env
REBECCA_BASE_URL=https://rebecca.example.com
REBECCA_USERNAME=YOUR_ADMIN
REBECCA_PASSWORD=YOUR_PASSWORD
```

بعد از restart ربات، از پنل ادمین گزینه `🦋 اتصال پنل Rebecca` را باز کنید و `تست اتصال` را بزنید. سپس از `🔀 انتخاب پنل VPN فعال`، Rebecca را انتخاب کنید.

## HWID

ربات به‌صورت محافظه‌کارانه HWID را فعال می‌کند: OpenAPI پنل بررسی می‌شود و فقط اگر `hwid_limit`/HWID در API اعلام شده باشد مقدار سقف دستگاه به پنل ارسال و بعد از ساخت/تمدید دوباره خوانده و verify می‌شود. اگر HWID توسط نسخه نصب‌شده Rebecca پشتیبانی نشود، سفارشی با سقف دستگاه رد می‌شود تا محدودیت به‌صورت صوری فروخته نشود.

Rebecca به‌صورت رسمی REST API، subscription و مدیریت محدودیت ترافیک/انقضا، چند نود و پروتکل‌های VLESS/VMess/Trojan/Shadowsocks را ارائه می‌کند. برای جزئیات نسخه نصب‌شده، `/docs` یا `/openapi.json` خود پنل مرجع نهایی است.
