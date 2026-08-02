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

  /* ---- запуск ------------------------------------------------------ */
  function init() {
    injectSuits();
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
