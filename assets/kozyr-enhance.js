/* ========================================================================
   KOZYR — enhancement layer (v1) — behaviour
   - Считает вверх цифры рейкбека (.kf-rake) и Kozyr Score (.kf-score-num)
     при первом появлении в зоне видимости.
   - Вставляет масти-водяные знаки в ключевые секции.
   - Полностью уважает prefers-reduced-motion.
   - Namespaced (kz-*), запускается после рендера страницы, ничего не ломает.
   ======================================================================== */
(function () {
  'use strict';

  var reduce = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- easing + счётчик ------------------------------------------- */
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  function countUp(target, from, to, decimals, dur, apply, done) {
    var t0 = null;
    function frame(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min((ts - t0) / dur, 1);
      var v = from + (to - from) * easeOutCubic(p);
      apply(decimals ? v.toFixed(decimals) : String(Math.round(v)));
      if (p < 1) requestAnimationFrame(frame);
      else if (done) done();
    }
    requestAnimationFrame(frame);
  }

  /* ---- анимируем один Kozyr Score (.kf-score-num: "8.9") ---------- */
  function animScore(el) {
    if (el.dataset.kzDone) return;
    el.dataset.kzDone = '1';
    var to = parseFloat((el.textContent || '').replace(',', '.'));
    if (isNaN(to)) { el.classList.add('kz-lined'); return; }
    if (reduce) { el.classList.add('kz-lined'); return; }
    el.classList.add('kz-counting');
    countUp(el, 0, to, 1, 900,
      function (s) { el.textContent = s; },
      function () {
        el.textContent = to.toFixed(1);
        el.classList.remove('kz-counting');
        el.classList.add('kz-lined');
      });
  }

  /* ---- анимируем рейкбек (.kf-rake: "до&nbsp;70<em>%</em>") -------- */
  function animRake(el) {
    if (el.dataset.kzDone) return;
    /* берём первый текстовый узел — там префикс + число ("до 70") */
    var node = null;
    for (var i = 0; i < el.childNodes.length; i++) {
      if (el.childNodes[i].nodeType === 3 &&
          /\d/.test(el.childNodes[i].textContent)) {
        node = el.childNodes[i];
        break;
      }
    }
    if (!node) { el.dataset.kzDone = '1'; return; }
    var m = node.textContent.match(/^(\D*)(\d+)(.*)$/);
    if (!m) { el.dataset.kzDone = '1'; return; }
    el.dataset.kzDone = '1';
    var prefix = m[1], to = parseInt(m[2], 10), suffix = m[3] || '';
    if (reduce) return;
    el.classList.add('kz-counting');
    countUp(el, 0, to, 0, 950,
      function (s) { node.textContent = prefix + s + suffix; },
      function () {
        node.textContent = prefix + to + suffix;
        el.classList.remove('kz-counting');
      });
  }

  /* ---- наблюдатель: анимируем при въезде в зону видимости ---------- */
  function watch(selector, fn) {
    var els = document.querySelectorAll(selector);
    if (!els.length) return;
    if (reduce || !('IntersectionObserver' in window)) {
      els.forEach(fn);
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          fn(e.target);
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.4, rootMargin: '0px 0px -6% 0px' });
    els.forEach(function (el) { io.observe(el); });
  }

  /* ---- вставка мастей-водяных знаков ------------------------------- */
  var SUITS = ['\u2660', '\u2666', '\u2663', '\u2665']; /* ♠ ♦ ♣ ♥ */
  function injectSuits() {
    var secs = document.querySelectorAll(
      'section.hero, section.how, section.compare, ' +
      'section.match-sec, section.faq, section.final, ' +
      '.room-hero, .club-hero, section.reviews, section.related');
    var n = 0;
    secs.forEach(function (sec) {
      if (sec.querySelector(':scope > .kz-suit')) return;
      if (getComputedStyle(sec).position === 'static') {
        sec.style.position = 'relative';
      }
      sec.style.overflow = sec.style.overflow || 'clip';
      var s = document.createElement('span');
      s.className = 'kz-suit';
      s.setAttribute('aria-hidden', 'true');
      s.textContent = SUITS[n % SUITS.length];
      sec.insertBefore(s, sec.firstChild);
      n++;
    });
  }

  /* ---- на мобилке показываем карточки вместо широкой таблицы ------- */
  function mobileDefaults() {
    if (window.innerWidth > 640) return;
    /* уважаем явный выбор в URL (?view=table) */
    try {
      var params = new URLSearchParams(location.search);
      if (params.has('view')) return;
    } catch (e) {}
    var cardsBtn = document.getElementById('kf-view-cards');
    if (cardsBtn && cardsBtn.getAttribute('aria-pressed') !== 'true') {
      cardsBtn.click();
    }
  }

  /* ---- Мобильное оглавление статей: аккордеон ---------------------- */
  /* Работает на страницах обзоров (ua/clubs/*, ua/rooms/*),
     где есть блок .article-toc. На широких экранах ничего не меняет —
     всё поведение внутри @media (max-width: 720px) в CSS.               */
  function mobileTOC() {
    var toc = document.querySelector('.article-toc');
    if (!toc) return;
    var label = toc.querySelector('.article-toc-label');
    if (!label) return;

    /* добавляем «превью» активного пункта в шапке — показывается
       только на мобилке (стили в CSS), обновляется при смене активного */
    var current = document.createElement('span');
    current.className = 'kz-toc-current';
    current.setAttribute('aria-hidden', 'true');
    label.appendChild(current);

    function activeLinkText() {
      var a = toc.querySelector('a.active') || toc.querySelector('a');
      return a ? (a.textContent || '').trim() : '';
    }
    function syncCurrent() { current.textContent = activeLinkText(); }
    syncCurrent();

    /* активный пункт в этом проекте меняется скриптом на самой странице
       (ScrollSpy). Отслеживаем изменения атрибута class у ссылок. */
    var mo = new MutationObserver(syncCurrent);
    toc.querySelectorAll('a').forEach(function (a) {
      mo.observe(a, { attributes: true, attributeFilter: ['class'] });
    });

    /* доступность: делаем шапку кнопкой */
    label.setAttribute('role', 'button');
    label.setAttribute('tabindex', '0');
    label.setAttribute('aria-expanded', 'false');
    label.setAttribute('aria-controls', 'kz-toc-list');
    var list = toc.querySelector('ul');
    if (list) list.setAttribute('id', 'kz-toc-list');

    function isMobile() { return window.innerWidth <= 720; }
    function setOpen(open) {
      if (!isMobile()) {                     /* на десктопе всегда открыт */
        toc.removeAttribute('data-open');
        label.setAttribute('aria-expanded', 'true');
        return;
      }
      toc.setAttribute('data-open', open ? 'true' : 'false');
      label.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    setOpen(false);

    label.addEventListener('click', function (e) {
      if (!isMobile()) return;
      /* игнорируем клики по самому «current»-подтексту (там нечего кликать) */
      var isOpen = toc.getAttribute('data-open') === 'true';
      setOpen(!isOpen);
    });
    label.addEventListener('keydown', function (e) {
      if (!isMobile()) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        var isOpen = toc.getAttribute('data-open') === 'true';
        setOpen(!isOpen);
      }
      if (e.key === 'Escape') setOpen(false);
    });
    /* по тапу на пункт — сворачиваем аккордеон (переход по якорю) */
    toc.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () {
        if (isMobile()) setOpen(false);
      });
    });
    /* пересинхронизация при повороте экрана */
    window.addEventListener('resize', function () {
      if (!isMobile()) {
        toc.removeAttribute('data-open');
      } else if (!toc.hasAttribute('data-open')) {
        setOpen(false);
      }
    });
  }

  /* ---- Плашка «Принимает / не принимает вашу страну» --------------- */
  /* Ищет на странице любой элемент с атрибутом data-accept-countries,
     читает список кодов через запятую и рендерит внутрь него плашку,
     привязанную к текущему гео из KozyrGeo. Перерендер по событию.

     Пример разметки:
       <div class="room-accept" data-accept-countries="ua"></div>
     После инициализации внутри появится плашка со статусом и флагом. */
  var COUNTRY_LABELS = {
    ua: 'Украина', pl: 'Польша', de: 'Германия', cz: 'Чехия',
    ru: 'Россия', by: 'Беларусь', kz: 'Казахстан', md: 'Молдова',
    ge: 'Грузия', am: 'Армения', az: 'Азербайджан',
    lt: 'Литва', lv: 'Латвия', ee: 'Эстония',
    sk: 'Словакия', hu: 'Венгрия', ro: 'Румыния', bg: 'Болгария',
    at: 'Австрия', it: 'Италия', es: 'Испания', pt: 'Португалия',
    fr: 'Франция', nl: 'Нидерланды', be: 'Бельгия',
    gb: 'Великобритания', ie: 'Ирландия',
    tr: 'Турция', il: 'Израиль', cy: 'Кипр'
  };

  function acceptMarkerHtml(list) {
    var geo = (window.KozyrGeo && window.KozyrGeo.get) ? window.KozyrGeo.get() : null;
    if (!geo) return '';
    var label = COUNTRY_LABELS[geo];
    if (!label) return '';   /* не знаем страну — не мешаем */
    var accepted = list.indexOf(geo) !== -1;
    var flag = '<span class="fi fi-' + geo + '" aria-hidden="true"></span>';
    var title = accepted
      ? 'Партнёр работает с игроками из региона: ' + label
      : 'Партнёр не обслуживает игроков из региона: ' + label + '. Возможно потребуется VPN или проверка условий рума.';
    var mainText = accepted
      ? 'Доступен для ' + label
      : 'Недоступен для ' + label;
    var subText = accepted
      ? 'Регистрация и игра из вашего региона поддерживаются'
      : 'Партнёр не обслуживает игроков из этого региона';
    return '<span class="ka ' + (accepted ? 'ka--yes' : 'ka--no') + '" title="' + title + '">' +
      '<span class="ka__icon" aria-hidden="true">' +
        (accepted
          ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg>'
          : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 8v5M12 16.5v.5"/><circle cx="12" cy="12" r="9"/></svg>'
        ) +
      '</span>' +
      '<span class="ka__body">' +
        '<span class="ka__main">' + mainText + '</span>' +
        '<span class="ka__sub">' + subText + '</span>' +
      '</span>' +
      flag +
    '</span>';
  }

  function renderAcceptanceMarkers() {
    var nodes = document.querySelectorAll('[data-accept-countries]');
    if (!nodes.length) return;
    nodes.forEach(function (el) {
      var raw = el.getAttribute('data-accept-countries') || '';
      var list = raw.split(',').map(function (s) { return s.trim().toLowerCase(); }).filter(Boolean);
      var html = acceptMarkerHtml(list);
      el.innerHTML = html;
      /* если плашка пустая (гео не определилось) — прячем контейнер,
         чтобы не оставалось «дырки» в вёрстке */
      el.style.display = html ? '' : 'none';
    });
  }

  /* ---- Плавающая Telegram-кнопка ---------------------------------- */
  /* Появляется через 2 секунды после загрузки, ведёт в @kozyr_support.
     Если пользователь закрыл через ×, скрываем на 24 часа.
     На страницах с .kozyr-sticky-cta смещаем выше (на десктопе);
     на мобилке при наличии sticky-cta вовсе не показываем — там
     контакт-путь уже даётся другими средствами (FAQ, футер). */

  var TG_URL = 'https://t.me/kozyr_support';
  var TG_STORAGE_KEY = 'kozyr_tg_dismissed_until';
  var TG_HIDE_MS = 24 * 60 * 60 * 1000;  /* 24 часа */

  function tgDismissed() {
    try {
      var until = parseInt(localStorage.getItem(TG_STORAGE_KEY) || '0', 10);
      return until > Date.now();
    } catch (e) { return false; }
  }

  function tgSetDismissed() {
    try {
      localStorage.setItem(TG_STORAGE_KEY, String(Date.now() + TG_HIDE_MS));
    } catch (e) {}
  }

  function initTelegramWidget() {
    if (tgDismissed()) return;
    /* Отключаем на страницах где цель — реф-регистрация (лендинги),
       чтобы TG-виджет не отвлекал от главной задачи */
    if (document.body.hasAttribute('data-no-tg-widget')) return;
    /* если элемент уже существует — не дублируем */
    if (document.querySelector('.kz-tg')) return;

    var hasStickyCta = !!document.querySelector('.kozyr-sticky-cta');
    var isMobile = window.innerWidth <= 640;
    /* на мобилке при sticky-cta не показываем — нижняя полоса и так занята */
    if (isMobile && hasStickyCta) return;

    var wrap = document.createElement('div');
    wrap.className = 'kz-tg' + (hasStickyCta ? ' kz-tg--shifted' : '');
    wrap.setAttribute('aria-live', 'polite');
    wrap.innerHTML =
      '<a class="kz-tg__btn" href="' + TG_URL + '" target="_blank" rel="noopener"' +
        ' aria-label="Написать в Telegram @kozyr_support">' +
        '<svg class="kz-tg__icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
          '<path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221l-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.446 1.394c-.14.18-.357.295-.6.295-.002 0-.002 0-.003 0l.213-3.054 5.56-5.022c.24-.213-.054-.334-.373-.121l-6.869 4.326-2.96-.924c-.64-.203-.658-.64.135-.954l11.566-4.458c.538-.196 1.006.128.832.941z"/>' +
        '</svg>' +
        '<span class="kz-tg__label">Вопрос? Напишите нам</span>' +
      '</a>' +
      '<button class="kz-tg__close" type="button" aria-label="Скрыть на 24 часа">×</button>';

    document.body.appendChild(wrap);

    /* небольшая пауза, чтобы CSS transition сработал (mount → visible) */
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { wrap.classList.add('is-visible'); });
    });

    wrap.querySelector('.kz-tg__close').addEventListener('click', function (e) {
      e.preventDefault();
      wrap.classList.remove('is-visible');
      tgSetDismissed();
      /* удаляем после завершения анимации */
      setTimeout(function () { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); }, 240);
    });
  }

  /* ---- запуск ------------------------------------------------------ */
  function init() {
    injectSuits();
    mobileDefaults();
    mobileTOC();
    renderAcceptanceMarkers();
    /* Если гео ещё не пришло — перерендерим по приходу */
    if (window.KozyrGeo) {
      window.KozyrGeo.onReady(function () { renderAcceptanceMarkers(); });
    }
    window.addEventListener('kozyr:geo', renderAcceptanceMarkers);
    /* Telegram-widget показываем с задержкой, чтобы не спамить сразу */
    setTimeout(initTelegramWidget, 2000);
    /* даём странице отрисовать таблицу/карточки, затем анимируем цифры */
    watch('.kf-score-num', animScore);
    watch('.room-score-num, .club-score-num', animScore);
    watch('.kf-rake', animRake);
  }

  function boot() {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () {
        setTimeout(init, 250);
      });
    } else {
      setTimeout(init, 250);
    }
  }
  boot();
})();
