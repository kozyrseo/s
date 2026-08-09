# Favicon и Organization schema — обновление

Дата: 09.08.2026

## Причина

В выдаче Google логотип отображался как тонкая синяя буква `K` на прозрачном
фоне — на светлой теме читался как placeholder, на тёмной пропадал в фоне.
Помимо визуальной проблемы, разметка была ошибочной: в `publisher.logo` JSON-LD
был указан широкоформатный баннер `og-image.png` (1200×630), из-за чего Google
игнорировал эту разметку и брал fallback (favicon.ico).

## Что изменено

### 1. Новый набор иконок (корень сайта)

Синяя плашка `#2668FF` со скруглёнными углами (~16%) и белой `K` внутри.
Прозрачный фон вокруг плашки, чтобы корректно смотрелось на любом бэкграунде.

- `favicon.ico` — мультиразмерный (16/32/48/64)
- `favicon-16.png`, `favicon-32.png`, `favicon-48.png`, `favicon-96.png` — **новое**
- `favicon-16x16.png`, `favicon-32x32.png` — **новое**, дубли под старые ссылки в блоге
- `apple-touch-icon.png` (180×180)
- `icon-192.png`, `icon-512.png`

`favicon-48.png` критичен: 48px — минимальный размер, который Google берёт
для отображения в SERP. Раньше его не было, Google даунскейлил из чего попало.

### 2. Починена schema.org (13 файлов)

В `publisher.logo` (тип `ImageObject`) был указан широкоформатный
`og-image.png` вместо квадратного логотипа. Заменено на `icon-512.png`
(512×512) во всех местах:

- `automation/templates/article.html` — шаблон, чтобы новые статьи генерились правильно
- `ua/index.html`, `ua/uk/index.html`
- `ua/clubs/klubok/index.html`, `ua/uk/clubs/klubok/index.html`
- `ua/rooms/pokerbet/index.html`, `ua/uk/rooms/pokerbet/index.html`
- Все статьи в `ua/blog/*/index.html` и `ua/uk/blog/*/index.html`

`og-image.png` при этом остался на месте в `<meta property="og:image">` и
`<meta name="twitter:image">` — там широкий формат как раз нужен.

### 3. Добавлена Organization schema (2 файла)

На корневой странице `/index.html` и `/int/index.html` не было
`Organization` JSON-LD. Именно эти URL Google посещает первыми и берёт
как источник данных о бренде. Добавлен блок `@graph` с `Organization`
(с новым `icon-512.png` в `logo`) и `WebSite`.

### 4. Унифицированы favicon-ссылки в <head> (17 файлов)

Заменил разношёрстные блоки `<link rel="icon">` на единый канонический
набор с ссылками на все нужные размеры. Убрана битая ссылка на
несуществующий `favicon-32x32.png` из `ua/blog/index.html`.

Обновлённый блок:

```html
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16.png">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="icon" type="image/png" sizes="48x48" href="/favicon-48.png">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
```

## После деплоя

Google не обновляет favicon в SERP мгновенно — цикл 1-4 недели, иногда до 2
месяцев. Чтобы ускорить:

1. В Google Search Console → Проверка URL → https://kozyr.club/ua/ →
   «Запросить индексирование». Аналогично для главных страниц других языков.
2. Проверить schema.org: https://search.google.com/test/rich-results
3. Свой браузер кэширует `.ico` на месяцы — тестировать через incognito или
   после жёсткого сброса кэша.
