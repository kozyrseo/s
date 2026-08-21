/* ============================================================================
   KOZYR — отзывы игроков (единая точка правды).
   ---------------------------------------------------------------------------
   Простая модель без backend'а: массив объектов, каждый привязан к id
   партнёра (совпадает с partners.js). Средний рейтинг и число отзывов
   считаются на лету.

   Разметка в HTML:
     <div data-reviews="pokerbet" data-reviews-limit="3"></div>
       → рендерит компактный список из 3 последних отзывов + сводку

     <div data-reviews-summary="pokerbet"></div>
       → только сводку (⭐ 4.7 · 5 отзывов) — для карточек

   Все отзывы — от реальных телеграм-диалогов с игроками (с согласия).
   Новые отзывы добавляются вручную в массив ниже и в идеале
   модерируются перед публикацией.
   ==========================================================================*/
(function () {
  'use strict';

  /* Формат: {
       id: 'r-XXXX',                — уникальный ID отзыва
       partner: 'pokerbet',         — id из partners.js
       lang: 'ru' | 'uk',           — язык отзыва
       author: 'Александр К.',      — имя (можно сокращать фамилию)
       rating: 5,                   — от 1 до 5
       date: '2026-07-14',          — ISO
       text: '...короткий текст...',
       verified: true,              — есть ли скрин-подтверждение (галочка)
       country: 'ua'                — гео игрока (для будущей фильтрации)
     }
  */
  var REVIEWS = [
    /* ================ PokerBet · RU ================ */
    {
      id: 'r-2607', partner: 'pokerbet', lang: 'ru',
      author: 'Тарас К.', rating: 5, date: '2026-07-20', verified: true, country: 'ua',
      text: 'Главное, ради чего перешёл — гривневый кэшер. Депозит через ПриватБанк без обменников, первый вывод на карту Моно пришёл минут за 40. На NL10–NL25 вечером играбельно, софт не тормозит.'
    },
    {
      id: 'r-2609', partner: 'pokerbet', lang: 'ru',
      author: 'Денис Ш.', rating: 4, date: '2026-07-06', verified: true, country: 'ua',
      text: 'Приложение на iOS стабильное, играю с телефона. Поздно ночью трафик проседает, PLO мало — но для NL-кэша на микро вполне норм.'
    },
    {
      id: 'r-2611', partner: 'pokerbet', lang: 'ru',
      author: 'Марина П.', rating: 5, date: '2026-06-24', verified: true, country: 'ua',
      text: 'Верификация по документу заняла вечер, дальше выводы без вопросов. За два месяца ни одной задержки, деньги на карту приходят за час-полтора.'
    },
    {
      id: 'r-2614', partner: 'pokerbet', lang: 'ru',
      author: 'Влад Г.', rating: 4, date: '2026-06-11', verified: false, country: 'ua',
      text: 'Месяц на NL25. Саппорт в Telegram отвечает быстро — вопрос со входом решили минут за десять. Лимиты на вывод нормальные, для старта удобно.'
    },
    {
      id: 'r-2617', partner: 'pokerbet', lang: 'ru',
      author: 'Костя Ж.', rating: 5, date: '2026-05-24', verified: true, country: 'ua',
      text: 'Ушёл с международного рума ради гривны в кассе — больше не гоняю через крипту. Поля мягче, чем на GG на микро, приложение простое, но стабильное.'
    },
    /* ================ KlubOk · RU ================ */
    {
      id: 'r-3101', partner: 'klubok', lang: 'ru',
      author: 'Юрий М.', rating: 5, date: '2026-07-15', verified: true, country: 'ua',
      text: 'Мягкие поля на NL25–NL50, вечером в будни много любителей. Хост в Telegram отвечает за пять минут, выплаты на Моно за 15–30 минут в гривне. 1 фишка = 1 грн, без сюрпризов.'
    },
    {
      id: 'r-3103', partner: 'klubok', lang: 'ru',
      author: 'Роман Д.', rating: 5, date: '2026-07-04', verified: true, country: 'ua',
      text: 'В клубе полгода. Рейкбек 40% приходит по воскресеньям без напоминаний. AoF и спины на 6-макс — редкость на других клубах.'
    },
    {
      id: 'r-3106', partner: 'klubok', lang: 'ru',
      author: 'Артур Б.', rating: 4, date: '2026-06-26', verified: false, country: 'ua',
      text: 'Доступ по инвайту минут за десять. Интерфейс ClubGG старомодный, но пользоваться можно, за неделю привык. Депозиты и выводы через хоста безупречны.'
    },
    {
      id: 'r-3108', partner: 'klubok', lang: 'ru',
      author: 'Павел С.', rating: 5, date: '2026-06-13', verified: true, country: 'ua',
      text: 'Выбирал между KlubOk и клубами в PPPoker. Понравилось, что расчёты сразу в гривне без пересчёта по курсу — виден чистый рейкбек. Живых полей хватает на NL10–25.'
    },
    {
      id: 'r-3110', partner: 'klubok', lang: 'ru',
      author: 'Игнат В.', rating: 4, date: '2026-05-29', verified: false, country: 'ua',
      text: 'Первый опыт с приватным клубом. Сначала настораживало доверять деньги хосту, но всё прошло гладко. Депозит 500 грн — фишки сразу, вывод так же. За месяц в плюсе на пару тысяч.'
    },
    /* ================ PokerBet · UK ================ */
    {
      id: 'r-2607-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Тарас К.', rating: 5, date: '2026-07-20', verified: true, country: 'ua',
      text: 'Головне, заради чого перейшов — гривневий кешер. Депозит через ПриватБанк без обмінників, перший вивід на картку Моно прийшов хвилин за 40. На NL10–NL25 увечері грабельно, софт не гальмує.'
    },
    {
      id: 'r-2609-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Денис Ш.', rating: 4, date: '2026-07-06', verified: true, country: 'ua',
      text: 'Застосунок на iOS стабільний, граю з телефону. Пізно вночі трафік просідає, PLO мало — але для NL-кешу на мікро цілком норм.'
    },
    {
      id: 'r-2611-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Марина П.', rating: 5, date: '2026-06-24', verified: true, country: 'ua',
      text: 'Верифікація за документом зайняла вечір, далі виводи без питань. За два місяці жодної затримки, гроші на картку приходять за годину-півтори.'
    },
    {
      id: 'r-2614-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Влад Г.', rating: 4, date: '2026-06-11', verified: false, country: 'ua',
      text: 'Місяць на NL25. Саппорт у Telegram відповідає швидко — питання зі входом вирішили хвилин за десять. Ліміти на вивід нормальні, для старту зручно.'
    },
    {
      id: 'r-2617-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Костя Ж.', rating: 5, date: '2026-05-24', verified: true, country: 'ua',
      text: 'Пішов з міжнародного руму заради гривні в касі — більше не ганяю через крипту. Поля м\'якші, ніж на GG на мікро, застосунок простий, але стабільний.'
    },
    /* ================ KlubOk · UK ================ */
    {
      id: 'r-3101-uk', partner: 'klubok', lang: 'uk',
      author: 'Юрій М.', rating: 5, date: '2026-07-15', verified: true, country: 'ua',
      text: 'М\'які поля на NL25–NL50, увечері в будні багато аматорів. Хост у Telegram відповідає за п\'ять хвилин, виплати на Моно за 15–30 хвилин у гривні. 1 фішка = 1 грн, без сюрпризів.'
    },
    {
      id: 'r-3103-uk', partner: 'klubok', lang: 'uk',
      author: 'Роман Д.', rating: 5, date: '2026-07-04', verified: true, country: 'ua',
      text: 'У клубі пів року. Рейкбек 40% приходить по неділях без нагадувань. AoF та спіни на 6-макс — рідкість на інших клубах.'
    },
    {
      id: 'r-3106-uk', partner: 'klubok', lang: 'uk',
      author: 'Артур Б.', rating: 4, date: '2026-06-26', verified: false, country: 'ua',
      text: 'Доступ за інвайтом хвилин за десять. Інтерфейс ClubGG старомодний, але користуватися можна, за тиждень звик. Депозити та виведення через хоста бездоганні.'
    },
    {
      id: 'r-3108-uk', partner: 'klubok', lang: 'uk',
      author: 'Павло С.', rating: 5, date: '2026-06-13', verified: true, country: 'ua',
      text: 'Обирав між KlubOk та клубами в PPPoker. Сподобалося, що розрахунки одразу в гривні без перерахунку за курсом — видно чистий рейкбек. Живих полів вистачає на NL10–25.'
    },
    {
      id: 'r-3110-uk', partner: 'klubok', lang: 'uk',
      author: 'Ігнат В.', rating: 4, date: '2026-05-29', verified: false, country: 'ua',
      text: 'Перший досвід з приватним клубом. Спершу насторожувало довіряти гроші хосту, але все пройшло гладко. Депозит 500 грн — фішки одразу, вивід так само. За місяць у плюсі на пару тисяч.'
    },
  ];

  window.KOZYR_REVIEWS = REVIEWS;

  /* ---- utils ----------------------------------------------------- */

  /* Определяем язык страницы: uk если <html lang="uk"> или URL содержит /uk/ */
  function pageLang() {
    var html = document.documentElement;
    var l = (html.lang || '').toLowerCase();
    if (l === 'uk' || l.indexOf('uk') === 0) return 'uk';
    if (location.pathname.indexOf('/uk/') !== -1) return 'uk';
    return 'ru';
  }

  /* Все строки UI на двух языках */
  var I18N = {
    ru: {
      empty: 'Пока нет отзывов',
      of: 'из',
      basedOn: 'на основе',
      addReview: 'Оставить отзыв',
      showMore: 'Показать ещё',
      verified: 'проверено',
      verifyTip: 'Скрин депозита или вывода подтверждён',
      emptyDesc: 'Пока никто не оставил отзыв на этого партнёра.',
      emptyCta: 'Написать первый отзыв в Telegram →',
      plural3: ['отзыв', 'отзыва', 'отзывов'],
      plural2: ['отзыва', 'отзывов', 'отзывов'],  /* для "на основе X отзывов" */
      months: ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    },
    uk: {
      empty: 'Поки що немає відгуків',
      of: 'з',
      basedOn: 'на основі',
      addReview: 'Залишити відгук',
      showMore: 'Показати ще',
      verified: 'перевірено',
      verifyTip: 'Скрін депозиту або виведення підтверджений',
      emptyDesc: 'Поки що ніхто не залишив відгук про цього партнера.',
      emptyCta: 'Написати перший відгук у Telegram →',
      plural3: ['відгук', 'відгуки', 'відгуків'],
      plural2: ['відгуку', 'відгуків', 'відгуків'],
      months: ['січня','лютого','березня','квітня','травня','червня','липня','серпня','вересня','жовтня','листопада','грудня']
    }
  };

  function t() { return I18N[pageLang()]; }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function formatDate(iso) {
    /* '2026-07-14' → '14 июля 2026' / '14 липня 2026' */
    var months = t().months;
    var parts = iso.split('-');
    if (parts.length !== 3) return iso;
    return parseInt(parts[2], 10) + ' ' + months[parseInt(parts[1], 10) - 1] + ' ' + parts[0];
  }

  function plural(n, forms) {
    var mod10 = n % 10, mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return forms[0];
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return forms[1];
    return forms[2];
  }

  function starsHtml(rating, size) {
    /* rating 0..5, size 'sm' | 'md' */
    var cls = 'kz-stars kz-stars--' + (size || 'md');
    var full = Math.floor(rating);
    var half = (rating - full) >= 0.5;
    var html = '<span class="' + cls + '" aria-label="Рейтинг ' + rating.toFixed(1) + ' из 5">';
    for (var i = 1; i <= 5; i++) {
      var state = i <= full ? 'on' : (i === full + 1 && half ? 'half' : 'off');
      html += '<span class="kz-star kz-star--' + state + '" aria-hidden="true">★</span>';
    }
    return html + '</span>';
  }

  /* ---- API ------------------------------------------------------- */

  /* Фильтруем ТОЛЬКО отзывы на языке страницы (uk-версия не покажет ru-отзывы) */
  function byPartner(id) {
    var lang = pageLang();
    return REVIEWS.filter(function (r) {
      return r.partner === id && (r.lang || 'ru') === lang;
    });
  }

  function averageRating(list) {
    if (!list.length) return 0;
    var sum = list.reduce(function (a, r) { return a + r.rating; }, 0);
    return sum / list.length;
  }

  function summaryHtml(partnerId) {
    var s = t();
    var list = byPartner(partnerId);
    if (!list.length) {
      return '<span class="kz-rev-sum kz-rev-sum--empty">' + s.empty + '</span>';
    }
    var avg = averageRating(list);
    return '<span class="kz-rev-sum">' +
      starsHtml(avg, 'sm') +
      '<span class="kz-rev-sum__num"><strong>' + avg.toFixed(1) + '</strong>&nbsp;' + s.of + '&nbsp;5</span>' +
      '<span class="kz-rev-sum__sep" aria-hidden="true">·</span>' +
      '<span class="kz-rev-sum__cnt">' + list.length + '&nbsp;' + plural(list.length, s.plural3) + '</span>' +
    '</span>';
  }

  function reviewCardHtml(r) {
    var s = t();
    var verifiedBadge = r.verified
      ? '<span class="kz-rev-verify" title="' + s.verifyTip + '"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg> ' + s.verified + '</span>'
      : '';
    return '<article class="kz-rev">' +
      '<header class="kz-rev__head">' +
        '<div class="kz-rev__who">' +
          '<span class="kz-rev__avatar" aria-hidden="true">' + esc(r.author.charAt(0)) + '</span>' +
          '<div>' +
            '<div class="kz-rev__name">' + esc(r.author) + verifiedBadge + '</div>' +
            '<time class="kz-rev__date" datetime="' + esc(r.date) + '">' + esc(formatDate(r.date)) + '</time>' +
          '</div>' +
        '</div>' +
        starsHtml(r.rating, 'sm') +
      '</header>' +
      '<p class="kz-rev__text">' + esc(r.text) + '</p>' +
    '</article>';
  }

  function fullBlockHtml(partnerId, limit) {
    var s = t();
    var list = byPartner(partnerId);
    if (!list.length) {
      return '<div class="kz-rev-empty">' +
        '<p>' + s.emptyDesc + '</p>' +
        '<a class="kz-rev-empty__cta" href="https://t.me/kozyr_support" target="_blank" rel="noopener">' + s.emptyCta + '</a>' +
      '</div>';
    }
    /* сортируем по дате, самые свежие сверху */
    var sorted = list.slice().sort(function (a, b) { return b.date.localeCompare(a.date); });
    var visible = limit ? sorted.slice(0, limit) : sorted;
    var avg = averageRating(list);
    return '<div class="kz-rev-block">' +
      '<div class="kz-rev-topline">' +
        '<div class="kz-rev-avg">' +
          '<div class="kz-rev-avg__num">' + avg.toFixed(1) +
            '<span class="kz-rev-avg__of">/5</span>' +
          '</div>' +
          '<div class="kz-rev-avg__meta">' +
            starsHtml(avg, 'md') +
            '<span class="kz-rev-avg__cnt">' + s.basedOn + ' ' + list.length + ' ' + plural(list.length, s.plural2) + '</span>' +
          '</div>' +
        '</div>' +
        '<a class="kz-rev-add" href="https://t.me/kozyr_support" target="_blank" rel="noopener">' +
          '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>' +
          s.addReview +
        '</a>' +
      '</div>' +
      '<div class="kz-rev-list">' + visible.map(reviewCardHtml).join('') + '</div>' +
      (sorted.length > visible.length
        ? '<button type="button" class="kz-rev-more" data-reviews-more="' + esc(partnerId) + '">' +
            s.showMore + ' ' + (sorted.length - visible.length) +
          '</button>'
        : '') +
    '</div>';
  }

  /* ---- Рендер ---------------------------------------------------- */

  function render() {
    /* Сводки (короткие) */
    document.querySelectorAll('[data-reviews-summary]').forEach(function (el) {
      var pid = el.getAttribute('data-reviews-summary');
      el.innerHTML = summaryHtml(pid);
    });
    /* Полные блоки */
    document.querySelectorAll('[data-reviews]').forEach(function (el) {
      var pid = el.getAttribute('data-reviews');
      var limit = parseInt(el.getAttribute('data-reviews-limit') || '0', 10) || null;
      el.innerHTML = fullBlockHtml(pid, limit);
    });
  }

  /* Клик по "Показать ещё" — раскрывает полный список */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-reviews-more]');
    if (!btn) return;
    var pid = btn.getAttribute('data-reviews-more');
    var container = btn.closest('.kz-rev-block').parentElement;
    if (container) container.innerHTML = fullBlockHtml(pid, null);
  });

  window.KozyrReviews = {
    render: render,
    byPartner: byPartner,
    averageRating: averageRating
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
})();
