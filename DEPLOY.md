# KOZYR — READY TO DEPLOY (всё вместе)

Один архив со ВСЕМИ изменениями: 5 улучшений (по мотивам PekarStas) + правки контактов
(убрана почта, соц-иконки → Telegram).

## Как деплоить

1. Распаковать этот архив
2. Скопировать содержимое **прямо в корень сайта** (там где лежат `index.html`, `sitemap.xml`, `assets/`)
3. Если файловый менеджер спрашивает «заменить?» — **ДА, заменять все**

## Что внутри

Структура **1:1 совпадает** со структурой сайта — файлы просто заместят старые:

```
assets/
  kozyr-geo.js         (новый)
  kozyr-reviews.js     (новый)
  kozyr-enhance.js     (замещение)
  kozyr-enhance.css    (замещение)
index.html             (замещение)
sitemap.xml            (замещение)
countries/index.html   (замещение)
int/index.html         (замещение)
ua/
  index.html           (замещение)
  blog/
    index.html         (замещение)
    kak-rabotayut-privatnye-pokerclubi/index.html
    pokerbet-ili-klubok-sravnenie/index.html
    chto-takoe-reykbek-v-pokere/index.html
  clubs/klubok/index.html
  legal/index.html
  rooms/
    pokerbet/index.html
    na-grivnu/index.html         (НОВАЯ ПАПКА)
    dlya-novichkov/index.html    (НОВАЯ ПАПКА)
    mobilnye/index.html          (НОВАЯ ПАПКА)
  uk/                  (все то же, но украинская локаль)
```

Всего файлов: 33 HTML + 4 модуля + sitemap.xml.

## Проверка после деплоя

- Открой https://kozyr.club/assets/kozyr-geo.js — должен отдать JS (не 404)
- Открой https://kozyr.club/assets/kozyr-reviews.js — то же самое
- Открой https://kozyr.club/ua/rooms/na-grivnu/ — новый лендинг
- Проверь футер главной — там должны быть только Telegram-ссылки, без почты

## Кэш

Если после деплоя новое не появляется — очисти CDN-кэш (Cloudflare → Caching → Purge Everything)
или проверь Ctrl+Shift+R в браузере.
