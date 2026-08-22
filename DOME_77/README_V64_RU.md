# DOME v64 PRODUCTION FIX

Постоянная production-схема снова единая: один Railway service запускает одновременно Telegram polling и Mini App HTTP server. Второй экземпляр с тем же BOT_TOKEN запускать нельзя.

## Что исправлено
- Сохранены исправления v63 для слишком большого мультфильма: Telegram-safe MP4, fallback, timeout отправки, COMPLETED только после фактической отправки.
- Railway PORT поддерживается автоматически: Mini App слушает переменную PORT, которую выдаёт Railway; локально остаётся WEBAPP_PORT=8080.
- RAILWAY_PUBLIC_DOMAIN автоматически используется как HTTPS base URL Mini App.
- Добавлен .dockerignore, чтобы Railway не отправлял в build context runtime storage, .env, кэши и архивы.
- Docker/Railway конфигурация приведена к одной production-схеме без WEB_ONLY и временных туннелей.
- Healthcheck Railway: /health.

## Важно
В production должен работать ровно один экземпляр Telegram polling. Перед включением Railway остановите локальный Docker-контейнер dome-bot, иначе TelegramConflictError вернётся.
