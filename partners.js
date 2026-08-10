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
      id: "pokerbet",
      name: "PokerBet",
      logo: { text: "PB", from: "#2668FF", to: "#1E52D9" },
      score: 8.2,
      rake: null,
      currency: "UAH",
      license: "Лицензия КРАИЛ",
      url: "/ua/rooms/pokerbet/",
      access: "direct",
      /* countries — страны для фильтра каталога (где партнёр представлен).
         acceptedCountries — страны, откуда партнёр ПРИНИМАЕТ игроков (для плашки
         «Доступен / Недоступен»). Для нового клуба достаточно задать эти два поля. */
      countries: ["ua"],
      acceptedCountries: ["ua"],
      limits: ["NL10", "NL25", "NL50", "NL100", "NL200"],
      games: ["cash", "mtt", "spins"],
      network: "pokerbet",
      payments: ["card", "bank"],
      software: ["win", "android", "ios", "web", "hud"],
      bonus: ["welcome", "freeroll"],
      payoutHours: 2,
      payoutLabel: "1–2 часа",
      note: "Единственный полностью легальный украинский рум. Налоги удерживаются автоматически.",
      card: {
        logoText: "P",
        logoImg: "/ua/blog/logos/pokerbet.webp",
        kind: "Легальный рум · КРАИЛ",
        dark: false,
        rows: [
          ["Рейкбек", "rake"],
          ["Валюта", "гривна (UAH)", false],
          ["Welcome-бонус", "до 40 000 ₴", true],
          ["Мин. депозит", "300 ₴", false],
          ["Форматы", "Hold'em, Omaha, Short Deck, MTT", false]
        ]
      }
    },
    {
      id: "klubok",
      name: "KlubOk",
      logo: { text: "KO", from: "#D9A93B", to: "#7E5512" },
      score: 7.9,
      rake: 40,
      currency: "UAH",
      license: "Офшорная юрисдикция",
      url: "/ua/clubs/klubok/",
      access: "club",
      /* Клуб принимает Украину + украиноязычную диаспору Европы (топ по численности).
         countries — для фильтра каталога; acceptedCountries — для плашки доступности. */
      countries: ["ua"],
      acceptedCountries: ["ua", "pl", "de", "cz", "it", "es", "ro", "sk", "md", "at", "fr", "gb", "nl", "pt", "hu", "be", "lt", "lv", "ee"],
      limits: ["NL10", "NL25", "NL50", "NL100"],
      games: ["cash", "mtt"],
      network: "clubgg",
      payments: ["card", "bank"],
      software: ["ios", "android", "win"],
      bonus: ["rakerace"],
      payoutHours: 1,
      payoutLabel: "15–60 минут",
      note: "Приватный клуб в ClubGG. Мягкие поля, расчёты в гривне через Telegram.",
      card: {
        logoText: "K",
        logoImg: "/ua/blog/logos/klubok.webp",
        kind: "Приватный клуб · ClubGG",
        dark: true,
        rows: [
          ["Рейкбек", "rake"],
          ["Валюта", "гривна (UAH)", false],
          ["Welcome-бонус", "100% на депозит", true],
          ["Доступ", "по инвайту", false],
          ["Верификация", "не требуется", false]
        ]
      }
    }
  ];

  window.KOZYR_PARTNERS = PARTNERS;

  function rakeText(p) {
    return (p.rake === null || p.rake === undefined) ? "уточняется" : (p.rake + "% еженедельно");
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
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
    return '<a href="' + esc(p.url) + '" class="pcard ' + (c.dark ? "pcard--club" : "pcard--room") + '">' +
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
      var ids = (box.getAttribute("data-ids") || "").trim();
      if (ids) {
        var order = ids.split(",").map(function (s) { return s.trim(); });
        list = order.map(function (id) {
          return PARTNERS.filter(function (p) { return p.id === id; })[0];
        }).filter(Boolean);
      } else {
        list.sort(function (a, b) { return (b.score || 0) - (a.score || 0); });
      }
      var limit = parseInt(box.getAttribute("data-limit") || "2", 10);
      list = list.slice(0, limit).filter(function (p) { return p.card; });
      box.style.gridTemplateColumns = "repeat(" + Math.min(list.length, 2) + ",1fr)";
      box.innerHTML = list.map(cardHTML).join("");
    });
  }
  window.renderPartnerCards = render;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
