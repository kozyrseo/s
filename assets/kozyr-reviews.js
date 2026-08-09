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
       author: 'Александр К.',      — имя (можно сокращать фамилию)
       rating: 5,                   — от 1 до 5
       date: '2026-07-14',          — ISO
       text: '...короткий текст...',
       verified: true,              — есть ли скрин-подтверждение (галочка)
       country: 'ua'                — гео игрока (для будущей фильтрации)
     }
  */
  var REVIEWS = [
    /* ================ PokerBet ================ */
    {
      id: 'r-2607',
      partner: 'pokerbet',
      author: 'Александр К.',
      rating: 5,
      date: '2026-07-14',
      text: 'Первый рум в Украине, где депозит в гривне через ПриватБанк без обменников. Верификация заняла полдня, играю уже второй месяц — выплаты стабильные, приходят на карту за час-два.',
      verified: true,
      country: 'ua'
    },
    {
      id: 'r-2609',
      partner: 'pokerbet',
      author: 'Дмитрий М.',
      rating: 4,
      date: '2026-07-02',
      text: 'Поля мягкие, особенно вечером на NL10-NL25. Трафика мало для мидстейкса, но для микро-лимитов норм. Софт удобный, есть версия для Android. Минус — иногда пусто на Омахе.',
      verified: true,
      country: 'ua'
    },
    {
      id: 'r-2611',
      partner: 'pokerbet',
      author: 'Виктор П.',
      rating: 5,
      date: '2026-06-25',
      text: 'Регулярю по 30 часов в неделю, вывел уже трижды — без вопросов, без задержек. Налоги удерживаются автоматически, никаких деклараций. Это огромный плюс для человека с белой зарплатой.',
      verified: true,
      country: 'ua'
    },
    {
      id: 'r-2614',
      partner: 'pokerbet',
      author: 'Игорь Н.',
      rating: 4,
      date: '2026-06-10',
      text: 'Депнул 500 грн, поднялся до 3к за две недели на NL10. Выводил через карту Моно — 20 минут. Приложение на iOS работает без багов. Для новичка в Украине — самый безопасный вариант.',
      verified: false,
      country: 'ua'
    },
    {
      id: 'r-2617',
      partner: 'pokerbet',
      author: 'Роман С.',
      rating: 5,
      date: '2026-05-22',
      text: 'Полностью легальная касса — для меня это решающий фактор. Раньше играл на GG через криптовыводы, было стрёмно перед налоговой. Здесь всё чисто. Поля слабее чем на GG, зато нервы целы.',
      verified: true,
      country: 'ua'
    },

    /* ================ KlubOk (ClubGG) ================ */
    {
      id: 'r-3101',
      partner: 'klubok',
      author: 'Максим Р.',
      rating: 5,
      date: '2026-07-18',
      text: 'Мягкие поля на NL25-NL50, много рекреационных игроков вечером в будни. Хост в Telegram отвечает за 5 минут, выплаты на карту Моно за 15-30 минут в гривнах. По факту 1 фишка = 1 грн, никаких сюрпризов.',
      verified: true,
      country: 'ua'
    },
    {
      id: 'r-3103',
      partner: 'klubok',
      author: 'Андрей Т.',
      rating: 5,
      date: '2026-07-05',
      text: 'Играю в клубе полгода. Рейкбек 40% начисляется по воскресеньям, приходит без напоминаний. Спины и AoF-столы на 6+ — большая редкость на других клубах.',
      verified: true,
      country: 'ua'
    },
    {
      id: 'r-3106',
      partner: 'klubok',
      author: 'Николай Б.',
      rating: 4,
      date: '2026-06-28',
      text: 'Плюс — никакой верификации, доступ по инвайту за 10 минут. Минус — интерфейс ClubGG выглядит старомодно, но за неделю привык. Депозиты и выводы через хоста работают идеально.',
      verified: false,
      country: 'ua'
    },
    {
      id: 'r-3108',
      partner: 'klubok',
      author: 'Владислав З.',
      rating: 5,
      date: '2026-06-14',
      text: 'Долго выбирал между KlubOk и другими клубами в PPPoker. Здесь понравилось, что расчёты сразу в гривне без пересчёта по курсу — виден чистый рейкбек. Поля живые, много любителей на NL10-25.',
      verified: true,
      country: 'ua'
    },
    {
      id: 'r-3110',
      partner: 'klubok',
      author: 'Евгений Х.',
      rating: 4,
      date: '2026-05-30',
      text: 'Первый опыт с приватным клубом. Немного страшно было доверять хосту с деньгами, но всё прошло гладко. Депозит 500 грн — сразу же появились фишки, вывод так же. По итогам месяца в плюсе на 2к грн.',
      verified: false,
      country: 'ua'
    }
  ];

  window.KOZYR_REVIEWS = REVIEWS;

  /* ---- utils ----------------------------------------------------- */

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function formatDate(iso) {
    /* '2026-07-14' → '14 июля 2026' */
    var months = ['января','февраля','марта','апреля','мая','июня',
                  'июля','августа','сентября','октября','ноября','декабря'];
    var parts = iso.split('-');
    if (parts.length !== 3) return iso;
    return parseInt(parts[2], 10) + ' ' + months[parseInt(parts[1], 10) - 1] + ' ' + parts[0];
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

  function byPartner(id) {
    return REVIEWS.filter(function (r) { return r.partner === id; });
  }

  function averageRating(list) {
    if (!list.length) return 0;
    var sum = list.reduce(function (a, r) { return a + r.rating; }, 0);
    return sum / list.length;
  }

  function summaryHtml(partnerId) {
    var list = byPartner(partnerId);
    if (!list.length) {
      return '<span class="kz-rev-sum kz-rev-sum--empty">Пока нет отзывов</span>';
    }
    var avg = averageRating(list);
    return '<span class="kz-rev-sum">' +
      starsHtml(avg, 'sm') +
      '<span class="kz-rev-sum__num"><strong>' + avg.toFixed(1) + '</strong> из 5</span>' +
      '<span class="kz-rev-sum__dot"></span>' +
      '<span class="kz-rev-sum__cnt">' + list.length + ' ' + plural(list.length, ['отзыв', 'отзыва', 'отзывов']) + '</span>' +
    '</span>';
  }

  function plural(n, forms) {
    var mod10 = n % 10, mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return forms[0];
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return forms[1];
    return forms[2];
  }

  function reviewCardHtml(r) {
    var verifiedBadge = r.verified
      ? '<span class="kz-rev-verify" title="Скрин депозита или вывода подтверждён"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg> проверено</span>'
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
    var list = byPartner(partnerId);
    if (!list.length) {
      return '<div class="kz-rev-empty">' +
        '<p>Пока никто не оставил отзыв на этого партнёра.</p>' +
        '<a class="kz-rev-empty__cta" href="https://t.me/kozyr_support" target="_blank" rel="noopener">Написать первый отзыв в Telegram →</a>' +
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
            '<span class="kz-rev-avg__cnt">на основе ' + list.length + ' ' + plural(list.length, ['отзыва', 'отзывов', 'отзывов']) + '</span>' +
          '</div>' +
        '</div>' +
        '<a class="kz-rev-add" href="https://t.me/kozyr_support" target="_blank" rel="noopener">' +
          '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>' +
          'Оставить отзыв' +
        '</a>' +
      '</div>' +
      '<div class="kz-rev-list">' + visible.map(reviewCardHtml).join('') + '</div>' +
      (sorted.length > visible.length
        ? '<button type="button" class="kz-rev-more" data-reviews-more="' + esc(partnerId) + '">' +
            'Показать ещё ' + (sorted.length - visible.length) +
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
