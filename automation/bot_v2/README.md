# KOZYR bot v2 — продвинутый автогенератор статей

Расширение существующего пайплайна `automation/` до полноценного
«бота-редакции» в Telegram. Работает поверх того, что уже есть, ничего не ломает.

## Что нового по сравнению с v1

| Возможность | v1 | v2 |
|---|---|---|
| Кнопки под превью: publish/regenerate/reject/fulltext | ✅ | ✅ (сохранены) |
| Кнопка «🧾 Исходники» — тема + метаданные + оценка качества | ❌ | ✅ |
| Кнопка «✏️ Правка» — редактирование meta_title/description и др. из TG | ❌ | ✅ |
| Команда `/suggested` — предложенные темы с кнопками | ❌ | ✅ |
| Команда `/generate N` — сгенерировать по конкретной строке таблицы | ❌ | ✅ |
| Команда `/research` — анализ ключей GSC + web_search → пополнение таблицы | ❌ | ✅ |
| Команда `/analytics` — отчёт по опубликованным статьям | ❌ | ✅ |
| Команда `/queue`, `/pending`, `/status`, `/history` | ❌ | ✅ |
| Автопоиск ключей раз в неделю по расписанию | ❌ | ✅ |
| Проставление `status=done` в Sheets после публикации | ❌ | ✅ |

## Архитектура

```
┌──────────────────┐    inline-кнопка / команда     ┌─────────────────────┐
│   Telegram       │ ────────────────────────────▶ │ Cloudflare Worker   │
│  (оператор)      │                                │  worker_v2.js       │
└──────────────────┘                                └──────────┬──────────┘
        ▲                                                       │
        │ превью + сводки                                       │ workflow_dispatch
        │                                                       │ + Contents API
        │                                                       ▼
┌──────────────────┐                                ┌─────────────────────┐
│  Google Sheets   │◀──── читает/пишет ───────────  │  GitHub Actions     │
│  (queue тем)     │                                │  - generate.py      │
└──────────────────┘                                │  - publish.py       │
        ▲                                            │  - keyword_researcher│
        │                                            │  - analytics.py     │
┌──────────────────┐                                └──────────┬──────────┘
│  GSC + web       │◀──── читает раз в неделю ─────────────────┘
│  (keyword src)   │                                            │
└──────────────────┘                                            │
                                                    ┌──────────▼──────────┐
                                                    │  Cloudflare Pages   │
                                                    │  (сайт kozyr.ua)    │
                                                    └─────────────────────┘
```

## Структура файлов

```
automation/
├── generate.py                  (v2: 3 ряда кнопок, source_row в meta)
├── publish.py                   (v2: _mark_source_row_done)
├── keyword_researcher.py        (новый — GSC + web_search + Sheets)
├── analytics.py                 (новый — GSC per-URL + классификация)
├── requirements.txt
│
├── bot_v2/
│   ├── __init__.py
│   ├── state.py                 (edit sessions, history, A/B, cache)
│   ├── suggested_topics.py      (CLI для approve/reject/dump/list)
│   ├── worker_v2.js             (расширенный Cloudflare Worker)
│   └── patches.py               (только для истории — что и где меняли)
│
.github/workflows/
├── generate-article.yml         (уже есть)
├── publish-article.yml          (уже есть)
├── reject-article.yml           (уже есть)
├── generate-from-row.yml        (новый — /generate N и кнопка ⚡ Генерить)
├── approve-topic.yml            (новый — /approve и кнопка ✅ В очередь)
├── reject-topic-row.yml         (новый — /rejectrow и кнопка ❌ Отклонить)
├── research-keywords.yml        (новый — /research и cron пн 09:00)
├── analytics-report.yml         (новый — /analytics и cron пн 10:00)
└── refresh-suggested.yml        (новый — снапшот Sheets каждый час)

.bot_state/                       (создаётся автоматически)
├── edit_sessions/{slug}.json     (активные правки, TTL 30 мин)
├── history/{slug}.json           (аудит всех действий по slug)
├── ab_tests/{slug}.json          (активные A/B тесты)
└── cache/
    ├── suggested_snapshot.json   (кэш suggested-тем для /suggested)
    └── queue_snapshot.json       (кэш queued-тем для /queue)
```

## Настройка (5 шагов)

### 1. Обновить Google Sheets

Добавь в первую строку (заголовки) недостающие колонки, если их ещё нет:

```
status | lang | topic | primary_keyword | secondary_keywords | intent | target_page | notes | source | evidence
```

Значения `status`:
- `suggested`  — предложено ботом или вручную, ждёт одобрения
- `queued`     — в очереди на генерацию
- `processing` — сейчас генерируется
- `pending_review` — в `_pending/`, ждёт публикации
- `done`       — опубликовано
- `rejected`   — отклонено

### 2. Обновить GitHub Secrets

К уже настроенным `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEETS_ID` — добавь:

- `GSC_SITE_URL` — URL сайта в GSC. Формат:
  - для «Domain property»: `sc-domain:kozyr.ua`
  - для «URL prefix»: `https://kozyr.ua/`

Проверь, что сервисный аккаунт добавлен в GSC как пользователь (Search Console
→ Settings → Users → Add User → email сервисного аккаунта → Restricted permission).

### 3. Обновить fine-grained PAT для Worker

Worker v2 теперь пишет в репозиторий (edit_sessions, snapshots, history).
Открой Settings → Developer settings → Fine-grained tokens → тот, что уже
используешь для Worker'а → Edit → Repository permissions:

- **Actions**: Read and write   ✅ (уже было)
- **Contents**: Read and write  ⬅ добавь, если было только Read

### 4. Задеплоить worker_v2.js

1. Открой в Cloudflare Dashboard тот же Worker, что использовался для v1
   (например `kozyr-telegram`).
2. Edit code → удали содержимое → вставь `automation/bot_v2/worker_v2.js`.
3. Deploy.
4. **Секреты Worker'а не меняются** (те же 5: BOT_TOKEN, CHAT_ID, SECRET_TOKEN,
   GITHUB_TOKEN, GITHUB_REPO). Вебхук пересоздавать не нужно.

### 5. Скопировать файлы в репозиторий и запушить

```bash
# В корне репозитория kozyrseo/s
cp -r output/automation/keyword_researcher.py automation/
cp -r output/automation/analytics.py         automation/
cp -r output/automation/bot_v2                automation/
cp    output/automation/generate.py          automation/generate.py
cp    output/automation/publish.py           automation/publish.py
cp    output/.github/workflows/*.yml         .github/workflows/

git add -A
git commit -m "bot v2: research + analytics + advanced Telegram controls"
git push
```

Сразу после пуша можно проверить:
- Actions → Refresh suggested/queue snapshots → Run workflow (создаст первые снапшоты).
- В TG отправь `/help` — бот покажет команды.

## Использование: типичные сценарии

### Сценарий A — «Ничего не делаю руками, бот сам»

Раз в неделю (понедельник 09:00 UTC) автоматически:
1. `research-keywords.yml` собирает 5-8 новых тем в Sheets со `status=suggested`.
2. Ты получаешь отчёт в TG: топ-5 тем на ревью.
3. Открываешь `/suggested`, нажимаешь `⚡ Генерить` на 2-3 понравившихся.
4. Через 3-5 минут приходят превью с оценкой качества → жмёшь ✅ Опубликовать.

Через час после research (10:00 UTC) — `analytics-report.yml`:
5. Смотришь сводку в TG: winners/needs_boost/flat.
6. По needs_boost решаешь: одну переписать, вторую оставить.

### Сценарий B — «У меня свои темы»

1. Ручную тему добавляешь в Google Sheets со `status=queued`.
2. В TG: `/generate` → бот берёт первую queued тему.
3. Превью → правка → публикация.

### Сценарий C — «Проверил превью, но заголовок не тот»

1. Нажимаешь `✏️ Правка` под превью.
2. Выбираешь поле: `meta_title` / `meta_description` / `h1_title` / `image_prompt` / `notes`.
3. Бот показывает текущее значение и просит прислать новое.
4. Отправляешь новое значение — бот применяет в `_pending/{slug}/meta.json` и подтверждает.
5. Нажимаешь ✅ Опубликовать — рендерится с новым значением.

### Сценарий D — «Хочу увидеть, что бот сгенерил и почему»

1. Нажимаешь `🧾 Исходники` под превью.
2. Бот присылает:
   - Тему из Sheets (topic, keywords, intent, notes)
   - Метаданные генерации (title, description, tags, word_count)
   - image_prompt, который пошёл в GPT
   - Отчёт `quality.json`: technical + content scores, предупреждения

## Команды бота (полный список)

```
/help                  — эта справка
/suggested [page]      — предложенные темы (по 5 на страницу)
/queue                 — темы в очереди на генерацию
/pending               — статьи в _pending, ждут публикации
/approve N             — строка N → queued
/rejectrow N           — строка N → rejected
/generate              — генерация из очереди
/generate N            — генерация по строке N
/research              — запустить keyword research вручную
/analytics             — сводка по опубликованным статьям
/status                — активные workflows + pending + edit sessions
/history slug          — история действий по статье
/edit slug             — меню полей для правки
/edit slug field       — сразу открыть редактор поля
/cancel                — отменить открытую сессию правки
```

## Разрешённые поля для /edit

Правка meta.json прямо из TG работает для:
- `meta_title`
- `meta_description`
- `h1_title`
- `image_prompt` (для регенерации через `/regenerate` берётся отсюда)
- `notes`
- `target_page`
- `primary_keyword` (для сохранения в topic_row_data)
- `secondary_keywords`

Остальные поля (тело статьи, faq, tags) правятся только через git или полную регенерацию.

## Как работает A/B

**Пока в бете.** Полноценный A/B в v2 реализован до уровня скелета:
- `state.start_ab_test(slug, "meta_title", variant_a, variant_b)` заводит тест
- на публикации через 15 дней бот меняет вариант
- через 30 дней смотрит CTR в GSC → фиксирует победителя

В `worker_v2.js` кнопка `/ab` пока напоминает про ручную правку меты.
Плановая доработка на следующую итерацию — интеграция с publish.py, чтобы
при `ab_test: true` в meta он рендерил вариант, соответствующий текущей ноге теста.

## Что делать если...

**«/suggested говорит: снапшот не собран»**
Запусти `Actions → Refresh suggested/queue snapshots → Run workflow`. Через минуту повтори.

**«Бот молчит на команды»**
Проверь `TELEGRAM_CHAT_ID` в секретах Worker'а — он должен совпадать с id чата,
где ты пишешь. `/status` из TG (если работает) покажет какие workflow'ы активны.

**«Правка не применилась»**
Сессия живёт 30 минут. Проверь `/status` — если сессия висит, отправь `/cancel`
и начни заново. Если сессия исчезла — писал в неправильный чат.

**«/research не находит новых тем»**
Проверь три условия:
1. `GSC_SITE_URL` задан в GitHub Secrets.
2. Service account добавлен в GSC (Settings → Users) с правами Restricted.
3. Сайт кopeкт-верифицирован в GSC (домен, а не URL-префикс — если у тебя `sc-domain:`).

**«Аналитика пустая (все статьи 0 показов)»**
GSC отдаёт данные с задержкой 2-3 дня. Первые статьи набирают импрессии
за 2-4 недели. Не паникуй если поначалу везде нули.

## Мониторинг

Все действия бота коммитятся в `.bot_state/history/{slug}.json`. Оттуда
всегда можно понять: кто/когда/что менял.

Все workflow-запуски видны в `Actions` — статус зелёный/красный, полный лог.

## Ограничения

- **Rate limit Google Sheets**: 60 запросов/минуту на service account. Обычно
  хватает, но при массовом одобрении (>50 строк подряд) может тормозить.
- **Rate limit GitHub Contents API**: 5000 запросов/час на PAT. Хватает.
- **TG callback_data**: 64 байта. Для приказов с длинным slug (> 50 символов)
  slug урезается — см. `slug_from_topic()` в generate.py.
- **Cloudflare Worker CPU**: 10 мс free / 50 мс paid. Все тяжёлые вещи —
  на GitHub Actions, Worker только диспетчер.

## Что дальше

Идеи для v3:
- Полноценный A/B (см. выше)
- Голосовые команды в TG (`/generate` через голосовое сообщение)
- Умный `regenerate`: не с нуля, а точечно (например, «перепиши только H2#3»)
- Комментарии оператора к статье и трек «что изменилось после ревью»
- Автоматический перевод winners-статей в другие языки
