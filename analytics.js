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
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a') : null;
    if (!a) return;

    var rel = (a.getAttribute('rel') || '').toLowerCase();
    var isAffiliate =
      rel.indexOf('sponsored') !== -1 ||
      a.classList.contains('js-aff') ||
      a.hasAttribute('data-aff');

    if (!isAffiliate) return;

    var label =
      a.getAttribute('data-aff') ||
      a.getAttribute('data-room') ||
      a.textContent.trim().slice(0, 60) ||
      a.href;

    gtag('event', 'affiliate_click', {
      link_url: a.href,
      link_label: label,
      page_path: location.pathname
    });
  }, false);
})();
