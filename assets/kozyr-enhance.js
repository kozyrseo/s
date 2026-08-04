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

  /* ---- запуск ------------------------------------------------------ */
  function init() {
    injectSuits();
    mobileDefaults();
    mobileTOC();
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
