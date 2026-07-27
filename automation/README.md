# KOZYR — автогенерация статей блога

Система пишет SEO-статьи для блога KOZYR (`/ua/blog/`) на русском:
Claude генерит текст + (опционально) картинку → статья кладётся в
`_pending/` → ты проверяешь → публикуешь → она рендерится в HTML,
обновляются sitemap и taxonomy.

Адаптировано из пайплайна PokerNet AI. Telegram-канал **заложен, но
выключен** (`TELEGRAM_ENABLED = False` в `lang_config.py`).

---

## Быстрый старт (без Google Sheets и Telegram)

Нужен только ключ Anthropic. Локально:

```bash
cd automation
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."      # обязательно
export OPENAI_API_KEY="sk-..."             # опционально (герой-картинка)

# 1. Генерируем статью из локальной темы
python generate.py --lang ru --topic-file topics/example-topic.json --force
#    → статья появится в _pending/<slug>/ (body.md + meta.json + quality.json)

# 2. Смотрим _pending/<slug>/body.md глазами, правим если надо

# 3. Публикуем — рендер в ua/blog/<slug>/, обновление sitemap+taxonomy
python publish.py --slug <slug> --lang ru

# 4. Коммитим и пушим — Cloudflare Pages задеплоит сам
git add -A && git commit -m "blog: <slug>" && git push
```

### Через GitHub Actions (кнопкой)
1. Задай секрет `ANTHROPIC_API_KEY` в Settings → Secrets → Actions.
2. Actions → **Generate article** → Run → укажи путь к теме → статья
   коммитится в `_pending/`.
3. Actions → **Publish article** → Run → введи slug → статья публикуется.

---

## Как задать тему

Тема — это JSON-файл в `topics/` (см. `topics/example-topic.json`):

```json
{
  "topic": "О чём статья (человеческим языком)",
  "primary_keyword": "главный ключ (вставится дословно)",
  "secondary_keywords": "ключ2, ключ3",
  "intent": "informational | commercial",
  "target_page": "/ua/",
  "notes": "Указания автору: акценты, что упомянуть, чего избегать"
}
```

Правила из промпта, которые важно помнить при постановке тем:
- Цифры PokerBet по рейкбеку/выводу — «уточняются». Не проси выдумывать.
- KOZYR — витрина, рейкбек платит рум/клуб. Промпт это соблюдает.
- Ставь `target_page` на реальную страницу: `/ua/`, `/ua/rooms/pokerbet/`
  или `/ua/clubs/klubok/`.

---

## Что где лежит

```
automation/
  generate.py          — генерация (Claude) → _pending/<slug>/
  publish.py           — рендер _pending → ua/blog/<slug>/ + sitemap + taxonomy
  quality_check.py     — авто-оценка качества (score 0-100)
  linking.py           — внутренняя перелинковка из taxonomy
  image_gen.py         — герой-картинка (нужен OPENAI_API_KEY)
  lang_config.py       — ★ГЛАВНЫЙ КОНФИГ: бренд, домен, UI, TELEGRAM_ENABLED
  system_prompt (в prompts/) — ★как Claude пишет статьи
  taxonomy.json        — реестр статей (пополняется при публикации)
  templates/article.html — HTML-шаблон статьи (тема KOZYR)
  topics/              — локальные темы (JSON)
  seed_topics/ru_topics_seed.csv — стартовые темы для Google Sheets
  backfill_related.py  — пересчёт блоков «Похожее» по всем статьям
  tg_autopost/         — автопостинг в Telegram-канал (пока не используется)
  _deferred/           — stats.py, pin_generate_button.py (нужны с Telegram/GSC)

ua/blog/kozyr-blog.css — стили блога (фирменная светлая тема)
```

Стили блога — в `/ua/blog/kozyr-blog.css`. Шаблон ссылается на них.

---

## Домен

В коде домен = `kozyr.ua` (плейсхолдер). После покупки замени во всём
проекте по инструкции в корневом `DEPLOY.md` (шаг 5). В автоматизации
домен лежит в `lang_config.py` → `SITE_URL`.

---

## Включить Telegram-канал позже

Когда захочешь, чтобы статьи анонсировались в Telegram-канал проекта и
приходили превью с кнопками «Опубликовать / Отклонить»:

1. Создай бота у @BotFather, узнай `chat_id` канала.
2. Добавь секреты `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. В `automation/lang_config.py` поставь `TELEGRAM_ENABLED = True`.
4. Для автопостинга готовых статей в канал — папка `tg_autopost/`
   (свой README внутри).

До этого весь Telegram-код просто пропускается — система работает без него.

---

## Опционально: очередь тем в Google Sheets

Если удобнее вести темы таблицей, а не файлами:
1. Создай Google-таблицу с колонками из `seed_topics/ru_topics_seed.csv`
   (`status,lang,topic,primary_keyword,...`).
2. Заведи service-account (Google Cloud), выдай доступ к таблице.
3. Секреты `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEETS_ID`.
4. Запускай `generate.py --lang ru` БЕЗ `--topic-file` — возьмёт первую
   строку со `status=queued`.

---

## Проверить окружение

```bash
python test_connections.py    # проверит доступные ключи/сервисы
```
