/* ============================================================================
   KOZYR — ЕДИНЫЙ источник данных о партнёрах (одна точка правды).
   Используется И финдером/калькулятором на главной, И карточками в блоге.
   Меняешь партнёра здесь → обновляется на главной и во всех статьях.

   Экспорт: window.KOZYR_PARTNERS  (массив со ВСЕМИ полями)
   Для блога: <div class="partners" data-partners data-limit="2"></div>
   ========================================================================== */
(function () {
  "use strict";

  var PARTNERS = [
  {
        "id": "pokerbet",
        "name": "PokerBet",
        "logo": {
              "text": "PB",
              "from": "#2668FF",
              "to": "#1E52D9"
        },
        "score": 8.2,
        "rake": "none",
        "currency": "UAH",
        "license": "Лицензия Curaçao",
        "url": "/ua/rooms/pokerbet/",
        "access": "direct",
        "countries": [
              "ua"
        ],
        "acceptedCountries": [
              "ua"
        ],
        "limits": [
              "NL10",
              "NL25",
              "NL50",
              "NL100",
              "NL200"
        ],
        "games": [
              "cash",
              "mtt",
              "spins"
        ],
        "network": "pokerbet",
        "payments": [
              "card",
              "bank"
        ],
        "software": [
              "win",
              "android",
              "ios",
              "web",
              "hud"
        ],
        "bonus": [
              "welcome",
              "freeroll"
        ],
        "payoutHours": 2,
        "payoutLabel": "1–2 часа",
        "note": "Покер-рум на гривны с лицензией Curaçao. Рейкбека нет — только бонусы.",
        "card": {
              "logoText": "P",
              "logoImg": "/ua/blog/logos/pokerbet.webp",
              "kind": "Покер-рум · Curaçao",
              "dark": false,
              "rows": [
                    [
                          "Рейкбек",
                          "rake",
                          false
                    ],
                    [
                          "Валюта",
                          "гривна (UAH)",
                          false
                    ],
                    [
                          "Welcome-бонус",
                          "до 40 000 ₴",
                          true
                    ],
                    [
                          "Мин. депозит",
                          "300 ₴",
                          false
                    ],
                    [
                          "Форматы",
                          "Hold'em, Omaha, Short Deck, MTT",
                          false
                    ]
              ]
        },
        "type": "room",
        "networkLabel": "PokerBet",
        "country": "ua"
  },
  {
        "id": "klubok",
        "name": "KlubOk",
        "logo": {
              "text": "KO",
              "from": "#D9A93B",
              "to": "#7E5512"
        },
        "score": 7.9,
        "rake": 40,
        "currency": "UAH",
        "license": "Офшорная юрисдикция",
        "url": "/ua/clubs/klubok/",
        "access": "club",
        "countries": [
              "ua"
        ],
        "acceptedCountries": [
              "ua",
              "pl",
              "de",
              "cz",
              "it",
              "es",
              "ro",
              "sk",
              "md",
              "at",
              "fr",
              "gb",
              "nl",
              "pt",
              "hu",
              "be",
              "lt",
              "lv",
              "ee"
        ],
        "limits": [
              "NL10",
              "NL25",
              "NL50",
              "NL100"
        ],
        "games": [
              "cash",
              "mtt"
        ],
        "network": "clubgg",
        "payments": [
              "card",
              "bank"
        ],
        "software": [
              "ios",
              "android",
              "win"
        ],
        "bonus": [
              "rakerace"
        ],
        "payoutHours": 1,
        "payoutLabel": "15–60 минут",
        "note": "Приватный клуб в ClubGG. Мягкие поля, расчёты в гривне через Telegram.",
        "card": {
              "logoText": "K",
              "logoImg": "/ua/blog/logos/klubok.webp",
              "kind": "Приватный клуб · ClubGG",
              "dark": true,
              "rows": [
                    [
                          "Рейкбек",
                          "rake",
                          false
                    ],
                    [
                          "Валюта",
                          "гривна (UAH)",
                          false
                    ],
                    [
                          "Welcome-бонус",
                          "100% на депозит",
                          true
                    ],
                    [
                          "Доступ",
                          "по инвайту",
                          false
                    ],
                    [
                          "Верификация",
                          "не требуется",
                          false
                    ]
              ]
        },
        "type": "club",
        "networkLabel": "ClubGG",
        "country": "ua"
  }
  ];

window.KOZYR_PARTNERS = PARTNERS;

  function rakeText(p) {
    if (p.rake === "none") return "нет";
    return (p.rake === null || p.rake === undefined) ? "уточняется" : (p.rake + "% еженедельно");
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  /* Язык текущей страницы: 'uk' для украинских страниц, иначе 'ru'.
     Определяем по <html lang> и по пути (/ua/uk/...). */
  function pageLang() {
    var htmlLang = (document.documentElement.getAttribute("lang") || "").toLowerCase();
    if (htmlLang.indexOf("uk") === 0) return "uk";
    if (/\/ua\/uk(\/|$)/.test(location.pathname)) return "uk";
    return "ru";
  }

  /* URL партнёра с учётом языка страницы.
     Приоритет: явный p.urlUk (если задан) → авто-локализация /ua/ → /ua/uk/.
     Для нового партнёра достаточно задать обычный url (/ua/...) — на UK-страницах
     ссылка локализуется автоматически, если UK-версия страницы существует по
     стандартному пути. Если структура нестандартная — задай p.urlUk явно. */
  function partnerUrl(p) {
    if (pageLang() !== "uk") return p.url;
    if (p.urlUk) return p.urlUk;
    // /ua/rooms/pokerbet/ → /ua/uk/rooms/pokerbet/  (но не трогаем уже-uk)
    if (/^\/ua\/(?!uk\/)/.test(p.url)) {
      return p.url.replace(/^\/ua\//, "/ua/uk/");
    }
    return p.url;
  }
  function cardHTML(p) {
    var c = p.card || {};
    var rows = (c.rows || []).map(function (r) {
      var label = r[0];
      var val = r[1] === "rake" ? rakeText(p) : r[1];
      var hi = r[1] === "rake" ? (p.rake !== null) : !!r[2];
      if (val === undefined || val === null || val === "") return "";
      return '<div class="pcard__row"><span class="pcard__k">' + esc(label) +
        '</span><span class="pcard__v' + (hi ? " hi" : "") + '">' + esc(val) + "</span></div>";
    }).join("");
    var logo;
    if (c.logoImg) {
      logo = '<div class="pcard__logo pcard__logo--img"><img src="' + esc(c.logoImg) +
        '" alt="' + esc(p.name) + '" width="44" height="44" loading="lazy"></div>';
    } else {
      logo = '<div class="pcard__logo" style="background:linear-gradient(135deg,' +
        esc(p.logo.from) + "," + esc(p.logo.to) + ')">' + esc(c.logoText || p.logo.text) + "</div>";
    }
    return '<a href="' + esc(partnerUrl(p)) + '" rel="sponsored" class="pcard ' + (c.dark ? "pcard--club" : "pcard--room") + '">' +
      '<div class="pcard__top">' + logo +
      '<div><div class="pcard__name">' + esc(p.name) + "</div>" +
      '<div class="pcard__badge">' + esc(c.kind || "") + "</div></div></div>" +
      '<div class="pcard__rows">' + rows + "</div>" +
      '<span class="pcard__cta">Смотреть обзор ' + esc(p.name) + " →</span></a>";
  }
  function render() {
    var boxes = document.querySelectorAll("[data-partners]");
    if (!boxes.length) return;
    boxes.forEach(function (box) {
      var list = PARTNERS.slice();

      // Фильтр по ТИПУ партнёра (для страниц-каталогов):
      //   data-partner-type="club"  → только клубы (access === "club")
      //   data-partner-type="room"  → только румы (всё остальное)
      // Масштабирование: добавляешь партнёра в PARTNERS выше — он автоматически
      // попадает в нужный каталог по своему access, без правки страниц.
      var ptype = (box.getAttribute("data-partner-type") || "").trim();
      if (ptype === "club") {
        list = list.filter(function (p) { return p.access === "club"; });
      } else if (ptype === "room") {
        list = list.filter(function (p) { return p.access !== "club"; });
      }

      var ids = (box.getAttribute("data-ids") || "").trim();
      if (ids) {
        // Явный список id перебивает фильтр (ручное переопределение для лендинга).
        var order = ids.split(",").map(function (s) { return s.trim(); });
        list = order.map(function (id) {
          return PARTNERS.filter(function (p) { return p.id === id; })[0];
        }).filter(Boolean);
      } else {
        // Фильтр по СВОЙСТВАМ (для лендингов — авто-наполнение).
        //   data-filter="currency:UAH"      → партнёры с валютой UAH
        //   data-filter="limit:NL10"        → у кого есть лимит NL10 (для новичков)
        //   data-filter="software:ios"      → у кого есть iOS (мобильные)
        //   data-filter="payments:crypto"   → кто принимает крипту
        //   data-filter="games:spins"       → у кого есть Spin&Go
        // Несколько условий через ; — все должны выполняться (AND).
        // Масштабирование: новый партнёр с currency=UAH сам появится на «на гривну».
        var filter = (box.getAttribute("data-filter") || "").trim();
        if (filter) {
          var conds = filter.split(";").map(function (s) { return s.trim(); }).filter(Boolean);
          // Алиасы: удобно писать в единственном числе, данные — во множественном.
          var KEY_ALIAS = { limit: "limits", game: "games", payment: "payments",
                            soft: "software", bonuses: "bonus" };
          list = list.filter(function (p) {
            return conds.every(function (cond) {
              var parts = cond.split(":");
              var key = (parts[0] || "").trim();
              var want = (parts[1] || "").trim();
              if (!key || !want) return true;
              key = KEY_ALIAS[key] || key;
              var val = p[key];
              // Массивы (limits, software, games, payments, bonus) — ищем вхождение.
              if (Array.isArray(val)) {
                return val.some(function (x) {
                  return String(x).toLowerCase() === want.toLowerCase();
                });
              }
              // Скаляр (currency, type, access) — сравниваем.
              return String(val == null ? "" : val).toLowerCase() === want.toLowerCase();
            });
          });
        }
        list.sort(function (a, b) { return (b.score || 0) - (a.score || 0); });
      }
      // data-limit="0" или отсутствие → показать ВСЕХ (для каталога).
      // Иначе — ограничить (для блока «топ-2» на главной/в блоге).
      var limitAttr = box.getAttribute("data-limit");
      var limit = limitAttr === null ? 0 : parseInt(limitAttr, 10);
      if (limit > 0) list = list.slice(0, limit);
      list = list.filter(function (p) { return p.card; });

      // Сетка: каталог (много карточек) — до 3 колонок; блок — до 2.
      var cols = ptype ? Math.min(list.length, 3) : Math.min(list.length, 2);
      box.style.gridTemplateColumns = "repeat(" + Math.max(cols, 1) + ",1fr)";
      box.innerHTML = list.map(cardHTML).join("");
    });
  }
  window.renderPartnerCards = render;

  // ── Боковой виджет партнёра (sticky в статье) ──────────────────────────
  // Определяет партнёра статьи и рендерит его карточку в правой колонке.
  // Партнёр берётся (в порядке приоритета):
  //   1. из data-partner="klubok" на контейнере (если задан явно), ИЛИ
  //   2. из <meta name="kozyr:partner" content="klubok">, ИЛИ
  //   3. по совпадению <link rel="canonical"> ... но проще — по target-URL,
  //      который генератор кладёт в meta. Мы сопоставляем partner.url со
  //      значением из meta name="kozyr:target".
  // ── МАСШТАБИРУЕМОЕ определение партнёров статьи ────────────────────────
  // Партнёр(ы) статьи выводятся из её тегов-платформ + связи partner.network
  // (единая точка правды — массив PARTNERS выше). Логика приоритета:
  //   1. Явный id партнёра в тегах (platform:klubok) → этот партнёр
  //   2. Сравнение (meta kozyr:compare) → все партнёры из указанных/по сети
  //   3. Тег-СЕТЬ (platform:clubgg) → ВСЕ партнёры этой сети
  //      (сейчас один — KlubOk; добавишь второй ClubGG-клуб → покажутся оба)
  //   4. Ничего → инфо-статья, виджета нет
  // Теги статьи приходят из meta name="kozyr:platforms" (напр. "klubok" или
  // "clubgg" или "pokerbet,klubok"). Генератор проставляет их из platform:*-тегов.
  function _platformTags() {
    var m = document.querySelector('meta[name="kozyr:platforms"]');
    if (!m || !m.content) return [];
    return m.content.split(",").map(function (s) { return s.trim(); })
      .filter(Boolean);
  }
  function resolvePartnersForPage() {
    var tags = _platformTags();

    // Явное сравнение (meta kozyr:compare) — обрабатывается отдельно в вызывающем
    // коде, но если сюда попали — вернём всех совпавших по id/сети.
    // 1. Явные партнёры: tag совпал с id партнёра
    var byId = PARTNERS.filter(function (p) { return tags.indexOf(p.id) !== -1; });
    // 2. Партнёры, чья СЕТЬ указана тегом (platform:clubgg → все clubgg-партнёры)
    var byNet = PARTNERS.filter(function (p) {
      return p.network && tags.indexOf(p.network) !== -1;
    });

    // Приоритет: если есть точное совпадение по id — берём его (обзор партнёра).
    // Иначе — по сети (общая статья про платформу). Дедупликация по id.
    var chosen = byId.length ? byId : byNet;
    var seen = {};
    return chosen.filter(function (p) {
      if (seen[p.id]) return false; seen[p.id] = 1; return true;
    });
  }

  function findPartnerForPage(box) {
    // 1. Явный id на самом контейнере
    var explicit = (box.getAttribute("data-partner") || "").trim();
    if (explicit) {
      var byId = PARTNERS.filter(function (p) { return p.id === explicit; })[0];
      if (byId) return byId;
    }
    // 2. meta name="kozyr:partner"
    var mp = document.querySelector('meta[name="kozyr:partner"]');
    if (mp && mp.content) {
      var byMeta = PARTNERS.filter(function (p) { return p.id === mp.content.trim(); })[0];
      if (byMeta) return byMeta;
    }
    // 3. Новый способ — по тегам-платформам (kozyr:platforms) + network
    var resolved = resolvePartnersForPage();
    if (resolved.length === 1) return resolved[0];
    // (несколько партнёров — обрабатывает renderSideWidget как список)
    // 4. meta name="kozyr:target" (= target_page статьи), сопоставляем с p.url
    var mt = document.querySelector('meta[name="kozyr:target"]');
    if (mt && mt.content) {
      var t = mt.content.trim().replace(/\/+$/, "");
      var byUrl = PARTNERS.filter(function (p) {
        return String(p.url).replace(/\/+$/, "") === t;
      })[0];
      if (byUrl) return byUrl;
    }
    return null;
  }

  // Компактная карточка для боковой колонки (чуть плотнее основной).
  function sideCardHTML(p) {
    var c = p.card || {};
    var rows = (c.rows || []).map(function (r) {
      var label = r[0];
      var val = r[1] === "rake" ? rakeText(p) : r[1];
      var hi = r[1] === "rake" ? (p.rake !== null) : !!r[2];
      if (val === undefined || val === null || val === "") return "";
      return '<div class="pcard__row"><span class="pcard__k">' + esc(label) +
        '</span><span class="pcard__v' + (hi ? " hi" : "") + '">' + esc(val) + "</span></div>";
    }).join("");
    var logo;
    if (c.logoImg) {
      logo = '<div class="pcard__logo pcard__logo--img"><img src="' + esc(c.logoImg) +
        '" alt="' + esc(p.name) + '" width="40" height="40" loading="lazy"></div>';
    } else {
      logo = '<div class="pcard__logo" style="background:linear-gradient(135deg,' +
        esc(p.logo.from) + "," + esc(p.logo.to) + ')">' + esc(c.logoText || p.logo.text) + "</div>";
    }
    return '<a href="' + esc(partnerUrl(p)) + '" rel="sponsored" class="pcard side-pcard ' +
      (c.dark ? "pcard--club" : "pcard--room") + '">' +
      '<div class="pcard__top">' + logo +
      '<div><div class="pcard__name">' + esc(p.name) + "</div>" +
      '<div class="pcard__badge">' + esc(c.kind || "") + "</div></div></div>" +
      '<div class="pcard__rows">' + rows + "</div>" +
      '<span class="pcard__cta">Перейти на ' + esc(p.name) + " →</span></a>";
  }

  function renderSideWidget() {
    var boxes = document.querySelectorAll("[data-partner-widget]");
    if (!boxes.length) return;
    boxes.forEach(function (box) {
      // Режим сравнения: meta kozyr:compare="all" ИЛИ data-compare на контейнере
      // → показываем ОБЕ карточки (для статей-сравнений). В нейтральном
      // сравнении не выделяем одного партнёра, даём выбор из обоих.
      var cmpMeta = document.querySelector('meta[name="kozyr:compare"]');
      var cmpAttr = (box.getAttribute("data-compare") || "").trim();
      var cmpVal = cmpAttr || (cmpMeta && cmpMeta.content ? cmpMeta.content.trim() : "");
      if (cmpVal) {
        var ids;
        if (cmpVal === "all") {
          ids = PARTNERS.map(function (p) { return p.id; });
        } else {
          ids = cmpVal.split(",").map(function (s) { return s.trim(); });
        }
        var cards = ids.map(function (id) {
          return PARTNERS.filter(function (p) { return p.id === id; })[0];
        }).filter(function (p) { return p && p.card; });
        if (!cards.length) { box.style.display = "none"; return; }
        box.innerHTML =
          '<div class="side-widget__label">Площадки из сравнения</div>' +
          cards.map(sideCardHTML).join("");
        return;
      }
      // Режим по тегам-платформам: один или несколько партнёров.
      // resolvePartnersForPage учитывает id-теги и network (см. выше).
      var resolved = resolvePartnersForPage().filter(function (p) { return p.card; });
      if (resolved.length) {
        var label = resolved.length > 1
          ? "Площадки на этой платформе"
          : "Площадка из обзора";
        box.innerHTML =
          '<div class="side-widget__label">' + label + "</div>" +
          resolved.map(sideCardHTML).join("");
        return;
      }

      // Фолбэк на старый способ (data-partner / kozyr:partner / kozyr:target)
      var p = findPartnerForPage(box);
      if (!p || !p.card) {
        // Партнёра нет (обзорная статья / нет совпадения) — прячем виджет.
        box.style.display = "none";
        return;
      }
      box.innerHTML =
        '<div class="side-widget__label">Площадка из обзора</div>' +
        sideCardHTML(p);
    });
  }
  window.renderPartnerSideWidget = renderSideWidget;

  // ── Мобильная липкая панель внизу экрана ───────────────────────────────
  // Показывается ТОЛЬКО на мобильном (CSS прячет её на десктопе, где есть
  // боковой виджет). Партнёр — тот же, что для бокового виджета.
  // Появляется после небольшой прокрутки, чтобы не мешать на первом экране.
  function rakeShort(p) {
    if (p.rake === "none") return null;
    return (p.rake === null || p.rake === undefined) ? null : ("рейкбек до " + p.rake + "%");
  }
  function renderMobileBar() {
    // В режиме сравнения панель НЕ показываем (обе карточки видны в тексте).
    var cmpMeta = document.querySelector('meta[name="kozyr:compare"]');
    if (cmpMeta && cmpMeta.content && cmpMeta.content.trim()) return;

    // Если статья относится к НЕСКОЛЬКИМ партнёрам (общая про платформу) —
    // панель тоже не показываем: в неё нельзя честно вынести одного.
    var resolved = resolvePartnersForPage().filter(function (p) { return p.card; });
    if (resolved.length > 1) return;

    // Один партнёр — берём его (из resolve или из старых мета-тегов).
    var p = resolved.length === 1 ? resolved[0] : null;
    if (!p) {
      var fake = document.createElement("div");
      p = findPartnerForPage(fake);
    }
    if (!p || !p.card) return;  // обзорная статья / нет партнёра — панель не нужна

    var c = p.card || {};
    var logo;
    if (c.logoImg) {
      logo = '<span class="pbar__logo pbar__logo--img"><img src="' + esc(c.logoImg) +
        '" alt="' + esc(p.name) + '" width="30" height="30" loading="lazy"></span>';
    } else {
      logo = '<span class="pbar__logo" style="background:linear-gradient(135deg,' +
        esc(p.logo.from) + "," + esc(p.logo.to) + ')">' + esc(c.logoText || p.logo.text) + "</span>";
    }
    var rk = rakeShort(p);
    var sub = rk ? ('<span class="pbar__sub">' + esc(rk) + "</span>") : "";
    var bar = document.createElement("div");
    bar.className = "partner-bar";
    bar.setAttribute("data-partner-bar", "");
    bar.innerHTML =
      '<a href="' + esc(partnerUrl(p)) + '" rel="sponsored" class="pbar__inner">' +
      '<span class="pbar__info">' + logo +
      '<span class="pbar__txt"><span class="pbar__name">' + esc(p.name) + "</span>" + sub + "</span></span>" +
      '<span class="pbar__cta">Играть →</span></a>';
    document.body.appendChild(bar);

    // Появление после прокрутки на ~40% первого экрана
    function onScroll() {
      if (window.scrollY > window.innerHeight * 0.4) {
        bar.classList.add("show");
      } else {
        bar.classList.remove("show");
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }
  window.renderPartnerMobileBar = renderMobileBar;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      render();
      renderSideWidget();
      renderMobileBar();
    });
  } else {
    render();
    renderSideWidget();
    renderMobileBar();
  }
})();
