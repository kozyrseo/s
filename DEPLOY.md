# KOZYR — деплой (GitHub + Cloudflare Pages + домен + аналитика)

Этот файл — пошаговая инструкция. Файлы конфигурации (`_headers`,
`_redirects`, `analytics.js`, `.gitignore`) уже лежат в проекте. Тебе
осталось выполнить действия в своих аккаунтах.

Ориентировочное время: 30–40 минут.

---

## 0. Что решить заранее: домен

Ты просил «анонимный домен». Честно про ограничения:

- **`.ua` не подойдёт для анонимности** — украинская зона требует
  идентификации владельца. Сейчас в конфигах прописан `kozyr.ua` как
  пример; при желании его можно оставить, но анонимным он не будет.
- **Реально приватные варианты:** зоны, где регистратор по умолчанию
  включает WHOIS-privacy (`.com`, `.net`, `.io`, `.co` через Cloudflare
  Registrar, Njalla, Porkbun). **Njalla** — сервис, специально
  заточенный под приватность (регистрирует домен на себя, ты —
  бенефициар); оплата в т.ч. криптой.
- **Важно:** приватность WHOIS ≠ полная анонимность. Регистратор всё
  равно знает платёжные данные. Полная анонимность требует отдельных мер
  и упирается в правила регистраторов и законы твоей юрисдикции — это
  твоя зона ответственности.

Определись с доменом ДО шага 4. Дальше в инструкции он обозначен как
`ВАШ-ДОМЕН`.

---

## 1. Залить проект на GitHub

```bash
cd путь/к/kozyr        # папка с index.html, ua/, int/ и т.д.
git init
git add -A
git commit -m "KOZYR: initial site"
```

Создай **приватный** репозиторий на github.com (кнопка New → Private),
затем:

```bash
git remote add origin git@github.com:ТВОЙ-ЛОГИН/kozyr.git
git branch -M main
git push -u origin main
```

> Приватный репозиторий скрывает исходники, но сам сайт после деплоя
> будет публичным — это нормально.

---

## 2. Подключить Cloudflare Pages (хостинг)

1. Заведи аккаунт на **dash.cloudflare.com** (бесплатно).
2. Слева: **Workers & Pages → Create → Pages → Connect to Git**.
3. Авторизуй GitHub, выбери репозиторий `kozyr`.
4. Настройки сборки:
   - **Framework preset:** `None`
   - **Build command:** оставить ПУСТЫМ (сайт статический, сборка не нужна)
   - **Build output directory:** `/` (корень)
5. **Save and Deploy.**

Через ~1 минуту сайт будет жить на адресе вида
`kozyr-xxx.pages.dev`. Проверь, что открывается и что гео-редирект
уводит на `/ua/`.

Дальше каждый `git push` в `main` = автоматический редеплой.

---

## 3. Google Analytics 4

1. **analytics.google.com** → Admin → Create Property.
2. Создай Property → Data Stream → **Web** → укажи будущий домен.
3. Скопируй **Measurement ID** (вид `G-XXXXXXXXXX`).
4. Открой файл `analytics.js` в корне проекта, замени:
   ```js
   var GA_MEASUREMENT_ID = 'G-XXXXXXXXXX'; // ← сюда свой ID
   ```
5. `git add analytics.js && git commit -m "Enable GA4" && git push`

Что уже настроено в `analytics.js`:
- просмотры страниц;
- событие **`affiliate_click`** — автоматически ловит клики по
  партнёрским кнопкам (любая ссылка с `rel="...sponsored"` или классом
  `.js-aff` / атрибутом `data-aff`) и пишет, куда вёл клик;
- `anonymize_ip` включён, Do-Not-Track уважается.

> Пока ID = плейсхолдер, скрипт ничего не грузит — статистика не
> засоряется на препродакшене.

---

## 4. Подключить домен

### 4a. Купить домен
Через любой из: Cloudflare Registrar (проще всего — уже в дашборде),
Porkbun, Njalla (для приватности). Дальше — `ВАШ-ДОМЕН`.

### 4b. Привязать к Cloudflare
- Если купил в **Cloudflare Registrar** — домен уже в аккаунте, шаг DNS
  автоматизирован.
- Если у стороннего регистратора — в Cloudflare: **Add a site →
  ВАШ-ДОМЕН**, затем пропиши у регистратора выданные Cloudflare
  **nameservers**. Подождать активации (до пары часов).

### 4c. Прицепить домен к Pages
Workers & Pages → твой проект → **Custom domains → Set up a custom
domain** → `ВАШ-ДОМЕН` и `www.ВАШ-ДОМЕН`. Cloudflare сам выпустит SSL.

### 4d. www → без www (и HTTPS)
Cloudflare → твой домен → **Rules → Redirect Rules → Create**:
- если хост = `www.ВАШ-ДОМЕН` → редирект 301 на
  `https://ВАШ-ДОМЕН/${path}`.

HTTPS-редирект: **SSL/TLS → Edge Certificates → Always Use HTTPS: On.**

---

## 5. Заменить домен в коде

В проекте домен зашит как `kozyr.ua` в canonical/hreflang/sitemap/robots.
После покупки замени одной командой (пример для macOS/Linux):

```bash
cd путь/к/kozyr
grep -rl "kozyr.ua" . --include="*.html" --include="*.xml" --include="*.txt" \
  | xargs sed -i '' 's/kozyr\.ua/ВАШ-ДОМЕН/g'   # на Linux убери ''
git add -A && git commit -m "Switch domain to ВАШ-ДОМЕН" && git push
```

Проверь после: `sitemap.xml`, `robots.txt`, `<link rel=canonical>` во
всех страницах указывают на новый домен.

---

## 6. Чек-лист перед публичным запуском

- [ ] `analytics.js`: вписан реальный `G-…` ID
- [ ] Домен заменён во всём коде (шаг 5)
- [ ] `KOZYR_DEMO = false` в каталоге на главной (`ua/index.html`) —
      выключить демо-румы
- [ ] Вторая партнёрская ссылка PokerBet вместо `%%POKERBET_URL%%`
      (`ua/rooms/pokerbet/index.html`)
- [ ] Sitemap отправлен в Google Search Console (после подключения домена)
- [ ] Проверить сайт на мобиле (меню-бургер, шторка фильтров)

---

## Заметки по безопасности

- Никаких секретов в репозитории. `.env`, service-account JSON и т.п.
  уже в `.gitignore`.
- Все партнёрские ссылки помечены `rel="nofollow sponsored"` и
  открываются в новой вкладке — это уже сделано в коде.
- Комплаенс (гэмблинг/аффилиатка, правила Cloudflare/регистратора,
  законы твоей юрисдикции) — на твоей стороне.
