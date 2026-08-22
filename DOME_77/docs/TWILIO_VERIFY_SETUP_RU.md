# Настройка SMS по ключам — Twilio Verify

В этой версии бот использует Twilio Verify. Он сам создаёт код, отправляет SMS и проверяет введённый код. Номер `TWILIO_FROM_PHONE` больше не нужен.

## Что нужно сделать один раз

1. Создать аккаунт Twilio и завершить проверку аккаунта.
2. Открыть Twilio Console → Account Info.
3. Скопировать `Account SID` и `Auth Token`.
4. Вставить в `.env`:

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=ваш_секретный_токен
TWILIO_VERIFY_SERVICE_SID=
TWILIO_VERIFY_FRIENDLY_NAME=DOME parental consent
```

При первой отправке SMS бот автоматически создаст Verify Service и сохранит его SID в `storage/twilio_verify_service_sid.txt`.

## Более безопасный вариант

Создайте в Twilio API Key и укажите:

```env
TWILIO_ACCOUNT_SID=AC...
TWILIO_API_KEY_SID=SK...
TWILIO_API_KEY_SECRET=...
TWILIO_AUTH_TOKEN=
```

## После изменения `.env`

```powershell
docker compose down
docker compose up --build
```

## Важно

- Номер родителя вводится в формате E.164: `+995...`, `+1...`, `+49...`.
- На trial-аккаунте Twilio могут действовать ограничения на получателей и страны.
- Разрешите нужные страны в Twilio Verify/Messaging Geo Permissions, если Twilio блокирует отправку.
- Секретные ключи нельзя присылать в чат и нельзя публиковать в GitHub.
