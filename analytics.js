/* ==========================================================================
   KOZYR — Google Analytics 4
   --------------------------------------------------------------------------
   Один общий файл для всех страниц. Чтобы включить аналитику:
     1. Заведи ресурс GA4 (analytics.google.com) → получи Measurement ID
        вида "G-XXXXXXXXXX".
     2. Впиши его ниже в GA_MEASUREMENT_ID.
   Пока ID = "G-XXXXXXXXXX" (плейсхолдер) — скрипт НИЧЕГО не грузит и не
   шлёт, чтобы на препродакшене не собиралась мусорная статистика.

   Отслеживаем автоматически:
     - просмотры страниц (стандартно)
     - клики по партнёрским кнопкам (событие "affiliate_click")
       — ловим любую ссылку с rel~="sponsored" или классом .js-aff /
         data-aff, передаём куда вёл клик (room/кнопку).
   ========================================================================== */
(function () {
  'use strict';

  var GA_MEASUREMENT_ID = 'G-E62CD39XNY'; // KOZYR — GA4 Measurement ID

  // --- gtag shim (определён ВСЕГДА, до всех проверок) ---
  // Стандартный паттерн Google: события пушатся в dataLayer-очередь.
  // Если gtag.js загружен — обрабатываются. Если нет — просто лежат в очереди.
  // Это позволяет affiliate_click handler ниже вызывать gtag() безопасно,
  // даже когда GA ещё не настроена (плейсхолдер ID) или пользователь не дал consent.
  window.dataLayer = window.dataLayer || [];
  window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };

  // Пока плейсхолдер — не инициализируем (чтобы не мусорить статистику).
  if (!GA_MEASUREMENT_ID || GA_MEASUREMENT_ID === 'G-XXXXXXXXXX') {
    return;
  }

  // Уважаем Do Not Track.
  if (navigator.doNotTrack === '1' || window.doNotTrack === '1') {
    return;
  }

  // --- Cookie consent (GDPR / ePrivacy) ---
  // Не грузим GA4 без явного согласия пользователя на аналитику.
  function hasAnalyticsConsent() {
    return window.KozyrConsent && window.KozyrConsent.hasConsent('analytics');
  }

  function initGA() {
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtag/js?id=' + GA_MEASUREMENT_ID;
    document.head.appendChild(s);
    window.gtag('js', new Date());
    window.gtag('config', GA_MEASUREMENT_ID, {
      anonymize_ip: true
    });
  }

  // Если консент уже дан — грузим сразу
  if (hasAnalyticsConsent()) {
    initGA();
  } else {
    // Ждём консент — banner отправит событие, если пользователь согласится
    window.addEventListener('kozyr:consent-ready', function (e) {
      if (e.detail && e.detail.categories && e.detail.categories.analytics === true) {
        initGA();
      }
    });
    // Если пользователь отклонил — тихо ничего не делаем, GA не грузится.
    // gtag()-события всё равно пушатся в dataLayer — но никуда не уходят.
  }

  // --- Трекинг кликов по партнёрским ссылкам ---
  // Ловим на этапе всплытия, чтобы не мешать переходу.
  // Считаем «партнёрским» клик, если ссылка помечена любым из:
  //   rel~="sponsored"  — SEO-разметка партнёрских ссылок
  //   .js-aff / data-aff — явная пометка
  //   data-partner       — внешние ссылки на трек-домен партнёра (klink на
  //                        страницах clubs/rooms)
  //   .klink             — класс партнёрских кнопок
  //   ссылка на /ua/clubs/<id>/ или /ua/rooms/<id>/ — внутренние переходы
  //                        на страницу партнёра из статей (виджет/панель/CTA)
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a') : null;
    if (!a) return;

    var rel = (a.getAttribute('rel') || '').toLowerCase();
    var href = a.getAttribute('href') || '';
    // Внутренняя ссылка на страницу партнёра (обзор → страница клуба/рума)
    var partnerPage = href.match(/\/ua\/(?:clubs|rooms)\/([a-z0-9-]+)\/?/i);

    var isAffiliate =
      rel.indexOf('sponsored') !== -1 ||
      a.classList.contains('js-aff') ||
      a.classList.contains('klink') ||
      a.hasAttribute('data-aff') ||
      a.hasAttribute('data-partner') ||
      !!partnerPage;

    if (!isAffiliate) return;

    // Тип клика: внешний переход к партнёру (outbound) vs внутренний на его
    // страницу-обзор (internal). Полезно разделять в отчётах GA4.
    var isOutbound = /^https?:\/\//i.test(href) &&
      href.indexOf(location.hostname) === -1;
    var clickType = isOutbound ? 'affiliate_click' : 'partner_page_click';

    // Метка-источник: откуда кликнули (виджет / мобильная панель / CTA / текст).
    // Определяем по ближайшему контейнеру, чтобы видеть, что конвертит.
    var source = 'link';
    if (a.closest('[data-partner-widget]')) source = 'side_widget';
    else if (a.closest('.partner-bar')) source = 'mobile_bar';
    else if (a.closest('.final-cta')) source = 'final_cta';
    else if (a.closest('.pcard')) source = 'partner_card';

    var label =
      a.getAttribute('data-aff') ||
      a.getAttribute('data-room') ||
      (partnerPage ? partnerPage[1] : '') ||
      a.textContent.trim().slice(0, 60) ||
      href;

    gtag('event', clickType, {
      link_url: a.href,
      link_label: label,
      link_source: source,
      page_path: location.pathname
    });
  }, false);
})();
