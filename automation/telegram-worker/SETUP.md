# KOZYR — управление публикацией через Telegram (Cloudflare Worker)

Схема ровно как на PokerNet: Telegram-кнопка → Cloudflare Worker →
workflow_dispatch в GitHub → нужный workflow. Без своего сервера.

```
GitHub генерит статью → присылает превью с кнопками в Telegram
   [✅ Опубликовать] [📄 Полный текст] [🔄 Перегенерить] [❌ Отклонить]
        ↓ (жмёшь кнопку)
Telegram → webhook (/webhook) → Cloudflare Worker
        ↓
Worker → POST actions/workflows/<файл>.yml/dispatches → GitHub
        ↓
publish-article.yml рендерит статью → Cloudflare Pages деплоит
```

---

## Шаг 1. Бот и chat_id
1. @BotFather → `/newbot` → получи **токен** (`TELEGRAM_BOT_TOKEN`).
2. Напиши боту любое сообщение, открой
   `https://api.telegram.org/bot<ТОКЕН>/getUpdates`, найди `"chat":{"id":...}`
   — это `TELEGRAM_CHAT_ID` (для канала начинается с `-100…`, бот — админ).

## Шаг 2. Секреты в GitHub
Repo `kozyrseo/s` → Settings → Secrets and variables → Actions:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
Теперь генерация будет слать превью в Telegram (TELEGRAM_ENABLED уже True).

## Шаг 3. GitHub PAT для Worker'а
Settings → Developer settings → Fine-grained tokens → Generate:
- Repository access: только `kozyrseo/s`
- Permissions → Actions: **Read and write**
Скопируй `github_pat_...` — это `GITHUB_TOKEN` для Worker'а.

## Шаг 4. Создать Worker
1. Cloudflare → Workers & Pages → Create → Workers → Create Worker →
   назови `kozyr-telegram` → Deploy.
2. Edit code → вставь весь `worker.js` из этой папки → Deploy.
3. URL воркера: `https://kozyr-telegram.<твой>.workers.dev`

## Шаг 5. Секреты Worker'а
Worker → Settings → Variables and Secrets (тип Secret):
- `TELEGRAM_BOT_TOKEN`   = токен бота
- `TELEGRAM_CHAT_ID`     = id чата (тот же, что в GitHub)
- `TELEGRAM_SECRET_TOKEN`= придумай случайную строку (для вебхука)
- `GITHUB_TOKEN`         = PAT из шага 3
- `GITHUB_REPO`          = `kozyrseo/s`
Deploy.

## Шаг 6. Привязать вебхук (ВАЖНО: путь /webhook)
Открой в браузере (подставь свои значения):
```
https://api.telegram.org/bot<ТОКЕН>/setWebhook?url=https://kozyr-telegram.<твой>.workers.dev/webhook&secret_token=<TELEGRAM_SECRET_TOKEN>&allowed_updates=%5B%22callback_query%22%5D
```
Ответ `{"ok":true}` = готово. Проверка: `.../getWebhookInfo`.

---

## Проверка
1. Actions → Generate article → Run.
2. Через ~2 мин в Telegram придёт превью с кнопками.
3. Жми ✅ Опубликовать → Worker дёрнет publish-article.yml → статья на сайте.

Кнопки: publish → publish-article.yml, regenerate → generate-article.yml,
reject → reject-article.yml, fulltext → присылает body.md в чат.

## Если не работает
- Превью не пришло → проверь TELEGRAM_* секреты в GitHub.
- Кнопка молчит → `getWebhookInfo` (ошибки?), секреты Worker'а, права PAT.
- «Доступ запрещён» → TELEGRAM_CHAT_ID в Worker'е не совпадает с чатом.
