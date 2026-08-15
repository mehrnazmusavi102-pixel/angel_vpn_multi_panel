"""
bot_loop.py
نگه‌داری یک رفرنس سراسری به event loop اصلی asyncio ربات — تا کدهای سینک
(مثل مسیر Flask کال‌بک پاسارگاد در bot.py) بتوانند یک coroutine را روی همان loop
اجرا کنند (asyncio.run_coroutine_threadsafe) — دقیقاً مثل fsm_storage.storage، یک reference سراسری
ساده که bot.py مقداردهی می‌کند.
"""

main_loop = None
