# KOZYR — 5 улучшений (по мотивам аудита PekarStas)

Дата сборки: 2026-08-09  
Всего файлов: 35 (4 новых модуля/скрипта + 25 модифицированных страниц + 6 новых landing)

---

## Что реализовано

### Пункт 1 — Плашка «Принимает / Не принимает вашу страну»
- **Новый модуль** `assets/kozyr-geo.js` — единая точка правды о стране пользователя
  (fetch к ipapi.co один раз в 24 часа, кэш в localStorage, CustomEvent `kozyr:geo`)
- **`assets/kozyr-enhance.js`** — добавлен `renderAcceptanceMarkers()` — универсальный маркер
  `[data-accept-countries="ua,pl"]` работает на любой странице
- **`assets/kozyr-enhance.css`** — стили `.ka`, `.ka--yes`, `.ka--no`, `.ka-block`
- **Главные страницы** (ru + uk) — расширен `DICT.countries` до 30 стран
  (СНГ + Балтия + EU), функция `acceptanceBadge()` встроена в `roomCell()`
- **Обзоры PokerBet и KlubOk** (ru + uk = 4 файла) — плашка `.ka-block` в верхнем блоке
  `room-facts` сразу под badge партнёра

### Пункт 2 — Автор в шапке блог-статьи
- **6 блог-статей** (3 ru + 3 uk) — компактный чип автора рядом с датой в `.post-hero__meta`
- CSS `.post-hero__author` вписан inline в каждую статью
- Никита Волошин (ru) / Микита Волошин (uk) — соответствует существующему автору в футере

### Пункт 3 — Sticky Telegram-widget
- Логика в `assets/kozyr-enhance.js` — функция `initTelegramWidget()`
- Стили `.kz-tg`, `.kz-tg__btn`, `.kz-tg__close` в `assets/kozyr-enhance.css`
- Появляется через 2 сек после загрузки, ведёт в `@kozyr_support`
- При клике × скрывается на 24 часа (localStorage: `kozyr_tg_dismissed_until`)
- На страницах с золотой `.kozyr-sticky-cta` смещается на 88px вверх
- На мобилке при наличии sticky-cta — вовсе не показывается (полоса занята)
- Работает на всех страницах, где подключен `kozyr-enhance.js`

### Пункт 4 — Отзывы игроков
- **Новый модуль** `assets/kozyr-reviews.js` — данные (10 отзывов: 5 PokerBet + 5 KlubOk) +
  функции рендера. API: `KozyrReviews.render()`, `.byPartner()`, `.averageRating()`
- **Два варианта отображения:**
  - `<div data-reviews="pokerbet" data-reviews-limit="3">` — полный блок со средним рейтингом,
    списком отзывов, кнопкой «Оставить отзыв» → Telegram, «Показать ещё»
  - `<div data-reviews-summary="pokerbet">` — компактная сводка «⭐ 4.6 из 5 · 5 отзывов»
- **CSS** в `assets/kozyr-enhance.css` — `.kz-stars`, `.kz-rev-*`, `.room-facts-reviews`
- **Обзоры PokerBet и KlubOk** (4 файла) — секция `#reviews` встроена между `pros-cons` и `cta`,
  пункт «Отзывы» добавлен в TOC, сводка в `room-facts`

### Пункт 5 — Гео-лендинги внутри Украины
- **6 новых страниц** (3 темы × 2 языка):
  - `/ua/rooms/na-grivnu/` + `/ua/uk/rooms/na-grivnu/` — Игра в гривне
  - `/ua/rooms/dlya-novichkov/` + `/ua/uk/rooms/dlya-novichkov/` — Для новичков
  - `/ua/rooms/mobilnye/` + `/ua/uk/rooms/mobilnye/` — Мобильные приложения
- Каждая страница: SEO-title с годом, canonical + hreflang, 3 JSON-LD блока
  (Organization + CollectionPage + FAQPage), карточки партнёров через `partners.js`
- **Скрипт** `build-landings.py` — можно пере-сгенерировать все страницы при изменении контента
- **`sitemap.xml`** — 6 новых URL добавлены
- **Главные страницы** (ru + uk) — секция «Тематические подборки / Тематичні добірки»
  добавлена после блока «Как это работает», перед секцией сравнения

---

## Файлы для деплоя

### JS/CSS (положить в `/assets/`):
```
assets/kozyr-geo.js       (новый)
assets/kozyr-reviews.js   (новый)
assets/kozyr-enhance.js   (обновлён — добавлены acceptance + tg-widget)
assets/kozyr-enhance.css  (обновлён — 4 новых блока стилей)
```

### HTML страницы:
- `pages/root_index.html`       → `/index.html`
- `pages/countries_index.html`  → `/countries/index.html`
- `pages/int_index.html`        → `/int/index.html`
- `pages/ua_index.html`         → `/ua/index.html`
- `pages/ua_uk_index.html`      → `/ua/uk/index.html`
- `pages/ua_blog_index.html`    → `/ua/blog/index.html`
- `pages/ua_uk_blog_index.html` → `/ua/uk/blog/index.html`
- `pages/ua_legal_index.html`   → `/ua/legal/index.html`
- `pages/ua_uk_legal_index.html`→ `/ua/uk/legal/index.html`
- `pages/ua_rooms_pokerbet.html`→ `/ua/rooms/pokerbet/index.html`
- `pages/ua_uk_rooms_pokerbet.html` → `/ua/uk/rooms/pokerbet/index.html`
- `pages/ua_clubs_klubok.html`  → `/ua/clubs/klubok/index.html`
- `pages/ua_uk_clubs_klubok.html`→ `/ua/uk/clubs/klubok/index.html`

### Обновлённые блог-статьи:
- `blog-articles/ua_privatnye-pokerclubi.html`   → `/ua/blog/kak-rabotayut-privatnye-pokerclubi/index.html`
- `blog-articles/ua_pokerbet-ili-klubok.html`    → `/ua/blog/pokerbet-ili-klubok-sravnenie/index.html`
- `blog-articles/ua_chto-takoe-reykbek.html`     → `/ua/blog/chto-takoe-reykbek-v-pokere/index.html`
- `blog-articles/uk_privatnye-pokerclubi.html`   → `/ua/uk/blog/kak-rabotayut-privatnye-pokerclubi/index.html`
- `blog-articles/uk_pokerbet-ili-klubok.html`    → `/ua/uk/blog/pokerbet-ili-klubok-sravnenie/index.html`
- `blog-articles/uk_chto-takoe-reykbek.html`     → `/ua/uk/blog/chto-takoe-reykbek-v-pokere/index.html`

### Новые гео-лендинги:
- `landings-new/ua_na-grivnu.html`      → `/ua/rooms/na-grivnu/index.html` (создать папку)
- `landings-new/uk_na-grivnu.html`      → `/ua/uk/rooms/na-grivnu/index.html`
- `landings-new/ua_dlya-novichkov.html` → `/ua/rooms/dlya-novichkov/index.html`
- `landings-new/uk_dlya-novichkov.html` → `/ua/uk/rooms/dlya-novichkov/index.html`
- `landings-new/ua_mobilnye.html`       → `/ua/rooms/mobilnye/index.html`
- `landings-new/uk_mobilnye.html`       → `/ua/uk/rooms/mobilnye/index.html`

### Корень:
- `sitemap.xml`         → заменить `/sitemap.xml`
- `build-landings.py`   → положить в корень (для будущих правок контента лендингов)

---

## Как протестировать после деплоя

### 1. Плашка acceptance
- Открыть `/ua/` — на карточках PokerBet и KlubOk должна появиться плашка справа
  (для UA-посетителей: «✓ принимает 🇺🇦»)
- Проверить через VPN (например, немецкий) — плашка станет «✕ не принимает 🇩🇪»
- Открыть `/ua/rooms/pokerbet/` — крупная плашка `.ka-block` в правом сайдбаре

### 2. Автор в блоге
- Открыть любую статью, например `/ua/blog/chto-takoe-reykbek-v-pokere/`
- В шапке под H1 должно быть: **[аватар] Никита Волошин · Рейкбек · 27 июля 2026 · 11 мин**

### 3. TG-widget
- На любой странице через 2 сек внизу справа появится голубая кнопка
  «Вопрос? Напишите нам» с иконкой Telegram
- Клик по × скрывает на 24 часа
- На `/ua/rooms/pokerbet/` — сдвинута выше золотой кнопки «Начать игру»

### 4. Отзывы
- Открыть `/ua/rooms/pokerbet/` — блок отзывов появится между «Плюсы и минусы» и «CTA»
- В правом сайдбаре в `room-facts` — компактная сводка «⭐ 4.6 из 5 · 5 отзывов»
- Клик по «Показать ещё» — раскрывает все 5

### 5. Гео-лендинги
- Открыть `/ua/rooms/na-grivnu/` — полноценный landing с h1, партнёрами, SEO-текстом, FAQ
- На `/ua/` в блоке «Тематические гиды» — 3 карточки-ссылки
- Проверить hreflang: смена языка ведёт на `/ua/uk/rooms/na-grivnu/`

---

## Что важно знать

1. **Никаких breaking changes** — весь новый код мягко деградирует. Если `KozyrGeo` не подключен,
   плашка acceptance просто не появится (контейнер скроется через `display:none`).
2. **Существующий `checkGeo()` на главной** оставлен нетронутым — работает параллельно с моим модулем.
3. **Все inline-JS в главных страницах** прошли валидацию через `node --check`.
4. **JSON-LD** во всех 6 лендингах и на обзорах — валиден (проверено json.loads).
5. **CSS** дополняет существующие стили, не переопределяет их.

---

## Дальнейшие шаги (для будущих спринтов)

- Заменить `og-image.png` (сейчас общий для всех) на индивидуальные `og-*.png` для каждого лендинга
- Добавить страницу `/ua/payments/` — каталог платёжных методов
- Публичный Telegram-бот `/deals` / `/calc`
- Расширить каталог: цель 6-8 румов + 3-4 клуба до конца квартала
