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
      author: 'Александр К.', rating: 5, date: '2026-07-14', verified: true, country: 'ua',
      text: 'Первый рум в Украине, где депозит в гривне через ПриватБанк без обменников. Верификация заняла полдня, играю уже второй месяц — выплаты стабильные, приходят на карту за час-два.'
    },
    {
      id: 'r-2609', partner: 'pokerbet', lang: 'ru',
      author: 'Дмитрий М.', rating: 4, date: '2026-07-02', verified: true, country: 'ua',
      text: 'Поля мягкие, особенно вечером на NL10-NL25. Трафика мало для мидстейкса, но для микро-лимитов норм. Софт удобный, есть версия для Android. Минус — иногда пусто на Омахе.'
    },
    {
      id: 'r-2611', partner: 'pokerbet', lang: 'ru',
      author: 'Виктор П.', rating: 5, date: '2026-06-25', verified: true, country: 'ua',
      text: 'Регулярю по 30 часов в неделю, вывел уже трижды — без вопросов, без задержек. Налоги удерживаются автоматически, никаких деклараций. Это огромный плюс для человека с белой зарплатой.'
    },
    {
      id: 'r-2614', partner: 'pokerbet', lang: 'ru',
      author: 'Игорь Н.', rating: 4, date: '2026-06-10', verified: false, country: 'ua',
      text: 'Депнул 500 грн, поднялся до 3к за две недели на NL10. Выводил через карту Моно — 20 минут. Приложение на iOS работает без багов. Для новичка в Украине — самый безопасный вариант.'
    },
    {
      id: 'r-2617', partner: 'pokerbet', lang: 'ru',
      author: 'Роман С.', rating: 5, date: '2026-05-22', verified: true, country: 'ua',
      text: 'Полностью легальная касса — для меня это решающий фактор. Раньше играл на GG через криптовыводы, было стрёмно перед налоговой. Здесь всё чисто. Поля слабее чем на GG, зато нервы целы.'
    },

    /* ================ PokerBet · UK ================ */
    {
      id: 'r-2607-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Олександр К.', rating: 5, date: '2026-07-14', verified: true, country: 'ua',
      text: 'Перший рум в Україні, де депозит у гривні через ПриватБанк без обмінників. Верифікація зайняла півдня, граю вже другий місяць — виплати стабільні, приходять на картку за годину-дві.'
    },
    {
      id: 'r-2609-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Дмитро М.', rating: 4, date: '2026-07-02', verified: true, country: 'ua',
      text: 'Поля м\'які, особливо ввечері на NL10-NL25. Трафіку мало для мідстейкса, але для мікро-лімітів норм. Софт зручний, є версія для Android. Мінус — іноді порожньо на Омасі.'
    },
    {
      id: 'r-2611-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Віктор П.', rating: 5, date: '2026-06-25', verified: true, country: 'ua',
      text: 'Регулярю по 30 годин на тиждень, вивів уже тричі — без питань, без затримок. Податки утримуються автоматично, ніяких декларацій. Це величезний плюс для людини з білою зарплатою.'
    },
    {
      id: 'r-2614-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Ігор Н.', rating: 4, date: '2026-06-10', verified: false, country: 'ua',
      text: 'Поклав 500 грн, піднявся до 3к за два тижні на NL10. Виводив через картку Моно — 20 хвилин. Застосунок на iOS працює без багів. Для новачка в Україні — найбезпечніший варіант.'
    },
    {
      id: 'r-2617-uk', partner: 'pokerbet', lang: 'uk',
      author: 'Роман С.', rating: 5, date: '2026-05-22', verified: true, country: 'ua',
      text: 'Повністю легальна каса — для мене це вирішальний фактор. Раніше грав на GG через криптовиводи, було лячно перед податковою. Тут все чисто. Поля слабші ніж на GG, зате нерви цілі.'
    },

    /* ================ KlubOk · RU ================ */
    {
      id: 'r-3101', partner: 'klubok', lang: 'ru',
      author: 'Максим Р.', rating: 5, date: '2026-07-18', verified: true, country: 'ua',
      text: 'Мягкие поля на NL25-NL50, много рекреационных игроков вечером в будни. Хост в Telegram отвечает за 5 минут, выплаты на карту Моно за 15-30 минут в гривнах. По факту 1 фишка = 1 грн, никаких сюрпризов.'
    },
    {
      id: 'r-3103', partner: 'klubok', lang: 'ru',
      author: 'Андрей Т.', rating: 5, date: '2026-07-05', verified: true, country: 'ua',
      text: 'Играю в клубе полгода. Рейкбек 40% начисляется по воскресеньям, приходит без напоминаний. Спины и AoF-столы на 6+ — большая редкость на других клубах.'
    },
    {
      id: 'r-3106', partner: 'klubok', lang: 'ru',
      author: 'Николай Б.', rating: 4, date: '2026-06-28', verified: false, country: 'ua',
      text: 'Плюс — никакой верификации, доступ по инвайту за 10 минут. Минус — интерфейс ClubGG выглядит старомодно, но за неделю привык. Депозиты и выводы через хоста работают идеально.'
    },
    {
      id: 'r-3108', partner: 'klubok', lang: 'ru',
      author: 'Владислав З.', rating: 5, date: '2026-06-14', verified: true, country: 'ua',
      text: 'Долго выбирал между KlubOk и другими клубами в PPPoker. Здесь понравилось, что расчёты сразу в гривне без пересчёта по курсу — виден чистый рейкбек. Поля живые, много любителей на NL10-25.'
    },
    {
      id: 'r-3110', partner: 'klubok', lang: 'ru',
      author: 'Евгений Х.', rating: 4, date: '2026-05-30', verified: false, country: 'ua',
      text: 'Первый опыт с приватным клубом. Немного страшно было доверять хосту с деньгами, но всё прошло гладко. Депозит 500 грн — сразу же появились фишки, вывод так же. По итогам месяца в плюсе на 2к грн.'
    },

    /* ================ KlubOk · UK ================ */
    {
      id: 'r-3101-uk', partner: 'klubok', lang: 'uk',
      author: 'Максим Р.', rating: 5, date: '2026-07-18', verified: true, country: 'ua',
      text: 'М\'які поля на NL25-NL50, багато рекреаційних гравців увечері в будні. Хост у Telegram відповідає за 5 хвилин, виплати на картку Моно за 15-30 хвилин у гривнях. Фактично 1 фішка = 1 грн, ніяких сюрпризів.'
    },
    {
      id: 'r-3103-uk', partner: 'klubok', lang: 'uk',
      author: 'Андрій Т.', rating: 5, date: '2026-07-05', verified: true, country: 'ua',
      text: 'Граю в клубі пів року. Рейкбек 40% нараховується по неділях, приходить без нагадувань. Спіни та AoF-столи на 6+ — велика рідкість на інших клубах.'
    },
    {
      id: 'r-3106-uk', partner: 'klubok', lang: 'uk',
      author: 'Микола Б.', rating: 4, date: '2026-06-28', verified: false, country: 'ua',
      text: 'Плюс — жодної верифікації, доступ по інвайту за 10 хвилин. Мінус — інтерфейс ClubGG виглядає старомодно, але за тиждень звик. Депозити та виводи через хоста працюють ідеально.'
    },
    {
      id: 'r-3108-uk', partner: 'klubok', lang: 'uk',
      author: 'Владислав З.', rating: 5, date: '2026-06-14', verified: true, country: 'ua',
      text: 'Довго обирав між KlubOk та іншими клубами в PPPoker. Тут сподобалося, що розрахунки одразу в гривні без перерахунку за курсом — видно чистий рейкбек. Поля живі, багато аматорів на NL10-25.'
    },
    {
      id: 'r-3110-uk', partner: 'klubok', lang: 'uk',
      author: 'Євген Х.', rating: 4, date: '2026-05-30', verified: false, country: 'ua',
      text: 'Перший досвід з приватним клубом. Трохи страшно було довіряти хосту з грошима, але все пройшло гладко. Депозит 500 грн — одразу з\'явилися фішки, виведення так само. За підсумками місяця у плюсі на 2к грн.'
    }
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
