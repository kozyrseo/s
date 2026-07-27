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

  var GA_MEASUREMENT_ID = 'G-XXXXXXXXXX'; // ← ЗАМЕНИ на свой ID

  // Пока плейсхолдер — не инициализируем (чтобы не мусорить статистику).
  if (!GA_MEASUREMENT_ID || GA_MEASUREMENT_ID === 'G-XXXXXXXXXX') {
    return;
  }

  // Уважаем Do Not Track.
  if (navigator.doNotTrack === '1' || window.doNotTrack === '1') {
    return;
  }

  // --- Стандартная загрузка gtag.js ---
  var s = document.createElement('script');
  s.async = true;
  s.src = 'https://www.googletagmanager.com/gtag/js?id=' + GA_MEASUREMENT_ID;
  document.head.appendChild(s);

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = gtag;
  gtag('js', new Date());
  gtag('config', GA_MEASUREMENT_ID, {
    anonymize_ip: true
  });

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
