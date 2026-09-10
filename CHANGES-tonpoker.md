# TON Poker → 10/10 + починка генератора

Пакет содержит только изменённые и новые файлы. Распакуй поверх репозитория
(пути сохранены). Ниже — что и зачем изменено.

## A. Страница TON Poker (доведение до 10/10)

1. **Sitemap** — `sitemap-raw.xml`
   Добавлены RU и UK URL (`/ua/rooms/tonpoker/`, `/ua/uk/rooms/tonpoker/`) с
   `lastmod/changefreq/priority` и hreflang-альтернативами, как у остальных
   румов. Раньше страницы вообще не было в sitemap.

2. **Отзывы / aggregateRating** — `ua/rooms/tonpoker/index.html`,
   `ua/uk/rooms/tonpoker/index.html`
   Прогнан `build_reviews.py`. Было: JSON-LD и статичная сводка = 4.4 / 5, а
   `reviews.json` и `kozyr-reviews.js` = 6 отзывов / 4.5 (после загрузки JS
   разметка расходилась с видимым). Стало: везде **4.5 / 6** согласованно.

3. **OG-картинка** — `og-rooms-tonpoker.jpg` (новый, 2400×1260)
   Раньше файла не существовало → битое превью при шеринге. Сгенерирована
   фирменная обложка новым скриптом `automation/build_partner_og.py`.

4. **llms.txt / llms-full.txt**
   TON Poker добавлен в оба файла (перегенерированы `build_llms.py`) + точная
   формулировка рейкбека «до 30% (выплата 1–3 числа месяца; MTT не в зачёт)»
   вместо неверного «30% еженедельно».

5. **Данные партнёра** — `partners.json` (+ пересобран `partners.js`)
   - логотип-инициалы `TO` → `TP`;
   - градиент лого `null/null` → TON-цвета `#0098EA → #0064B5`;
   - тёмная карточка `dark:false` → `true` (как и просила анкета `dark_card:true`);
   - добавлено поле `rakeText` с точной формулировкой рейкбека.

6. **Удалён** устаревший дубль `_pending_partner/tonpoker/` (был публично
   доступен; canonical и так вёл на боевой URL, но это лишний мусор).

## B. Блок партнёров в статьях (полный список вместо «топ-2»)

7. `automation/body_enhance.py` — дефолт блока `data-limit="2"` → `data-limit="0"`
   (все партнёры). Ограничение по-прежнему можно задать в статье через
   `[[partners:limit=N]]`.

8. `automation/partners.tail.js` (внутри пересобранного `partners.js`) — сетка
   блока в статье теперь адаптивная `auto-fit / minmax(220px)` (до 3 колонок на
   десктопе, 1 на мобиле) вместо форсированного числа колонок инлайном.

9. Обновлены **16** уже опубликованных статей (RU+UK): generic-блоки
   `data-limit="2"` → `data-limit="0"`. «Пиновые» блоки `data-ids=...` не тронуты.

   > Как результат — TON Poker теперь появляется в каждом блоке-каталоге статей,
   > в финдере на главной и в списке румов, получая внутренние ссылки (раньше на
   > страницу не вело ни одной внутренней ссылки).

## C. Генератор партнёров (чтобы следующая страница была идеальной сразу)

10. `automation/generate_partner_tpl.py` (основной генератор) и
    `automation/generate_partner.py` (устаревший) — исправлены два корневых бага
    в `build_partner_object`:
    - `dark_card`: сравнение булева со строкой (`True == "true"` → всегда `False`)
      → теперь через `as_bool()` (карточка честно тёмная, если анкета так просит);
    - инициалы лого: `name[:2]` («TON Poker» → «TO») → `logo_initials()` по словам
      и camelCase («TON Poker»→«TP», «PokerBet»→«PB», «KlubOk»→«KO»);
    - цвета лого: `.get(k, default)` при `null` в анкете возвращал `None` →
      теперь `.get(k) or default`;
    - проброс `rakeLabel` / `rakeText` из анкеты в `partners.json`.

11. `automation/generate_partner_tpl.py` — в `main()` добавлена **пропагация по
    сайту** после публикации (`_propagate`): обновление sitemap, `build_partner_og.py`
    (OG), `build_reviews.py` (отзывы/aggregateRating), `build_llms.py` (llms). Раньше
    эти шаги делались вручную и для нового партнёра забывались.

12. `automation/build_reviews.py`
    - `PAGES` теперь **выводится из `partners.json`** (по полю `url`), а не
      хардкодится — любой новый партнёр автоматически попадает под серверный
      рендер отзывов (главная причина рассинхрона у TON Poker);
    - партнёр **без отзывов**: теперь снимается зашитый в шаблон плейсхолдер
      `aggregateRating` (4.4/5 с чужими текстами) и чистятся маркеры — чтобы не
      отдавать Google фейковый рейтинг.

13. `automation/build_partners.py` — fallback-инициалы (`name[:2]`) заменены на
    тот же `logo_initials()`.

14. `automation/build_llms.py`
    - `rakeback_text()` теперь уважает явную формулировку (`rakeText`/`rakeLabel`)
      и не хардкодит «еженедельно»;
    - добавлено расширенное описание TON Poker для `llms-full.txt`.

15. `automation/partner_template.html.j2` — `og:image`/`twitter:image` стали
    type-aware: `og-{{ p.kind }}-{{ p.id }}.jpg` (rooms/clubs) вместо жёсткого
    `og-rooms-...`, иначе у клубов ссылка вела бы на несуществующий файл.

## Как применить / регенерировать

```bash
# после распаковки поверх репозитория — пересборки (идемпотентны):
python automation/build_partners.py        # partners.js из partners.json
python automation/build_reviews.py          # отзвыв/aggregateRating + JS
python automation/build_llms.py             # llms.txt / llms-full.txt
python automation/build_partner_og.py --all # OG-обложки всех партнёров

# новый партнёр целиком (теперь сам делает sitemap+OG+reviews+llms):
python automation/generate_partner_tpl.py --id <id> --publish
```
