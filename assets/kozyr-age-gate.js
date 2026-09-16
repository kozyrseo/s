/* ============================================================================
   KOZYR — Age-gate 21+
   ----------------------------------------------------------------------------
   Full-screen modal shown on first visit. User must confirm they are 21+
   before accessing the site (Ukrainian КРАИЛ requirement + EU compliance).

   • Detected language: RU / UK / EN via navigator.language + localStorage
   • Confirmation stored in localStorage — never shown again for that browser
   • "No" button redirects to begambleaware.org (responsible gaming resource)
   • Skips /legal/ pages so users can always read policy
   • SEO-safe: content under modal is fully rendered; Googlebot doesn't run JS
     so it never sees the modal — indexes the page normally
   • CSP-safe: no inline styles/scripts, no external resources
   ========================================================================== */
(function () {
  'use strict';

  var STORAGE_KEY = 'kozyr_age_confirmed';
  var STORAGE_KEY_TS = 'kozyr_age_confirmed_at';
  var REDIRECT_URL = 'https://www.begambleaware.org/';

  // ---- Skip conditions -----------------------------------------------------
  // Skip if user already confirmed
  try {
    if (localStorage.getItem(STORAGE_KEY) === 'true') return;
  } catch (e) { /* localStorage disabled — show modal anyway */ }

  // Skip on legal/policy pages (users must be able to read policies)
  if (/\/legal(\/|$)/.test(window.location.pathname)) return;

  // ---- Минимальный возраст по стране (гэмблинг) ---------------------------
  // Возраст берётся АВТОМАТИЧЕСКИ по коду страны из URL (/ua/…, /pl/… и т.д.).
  // При добавлении новой страны на сайт age-gate подхватит её сам, если она
  // есть в справочнике ниже. Справочник — по легальному возрасту азартных игр.
  // Источник по UA: Закон 768-IX ст.18 — 21 год. EU в основном 18.
  var AGE_BY_COUNTRY = {
    ua: 21,   // Украина — Закон 768-IX ст.18
    us: 21,   // США — в большинстве штатов 21
    pl: 18,   // Польша
    de: 18,   // Германия
    es: 18,   // Испания
    gb: 18,   // Великобритания
    uk: 18,   // (алиас Великобритании, если встретится код uk как страна)
    ca: 18,   // Канада (большинство провинций)
    nl: 18,   // Нидерланды
    cz: 18,   // Чехия
    ro: 18,   // Румыния
    pt: 18,   // Португалия
    it: 18,   // Италия
    fr: 18,   // Франция
    at: 18,   // Австрия
    ie: 18,   // Ирландия
    fi: 18,   // Финляндия
    md: 18,   // Молдова
    ge: 18    // Грузия
  };
  // Безопасный дефолт: если страны нет в справочнике — берём БОЛЕЕ СТРОГИЙ 21.
  // Лучше показать 21 там, где хватило бы 18 (чуть строже), чем наоборот
  // (пустить несовершеннолетнего = нарушение).
  var DEFAULT_AGE = 21;

  function detectCountry() {
    // Страна = первый сегмент пути: /ua/…, /pl/… . Надёжнее geo-IP (нет VPN,
    // нет стороннего запроса, совпадает с реальным гео-разделом контента).
    var seg = window.location.pathname.split('/').filter(Boolean)[0];
    if (seg) {
      seg = seg.toLowerCase();
      // сегмент 'uk' в URL — это украинская локаль (/ua/uk/), не страна;
      // страна определяется первым сегментом, а он для сайта = 'ua'.
      if (AGE_BY_COUNTRY.hasOwnProperty(seg)) return seg;
    }
    return null;
  }

  var minAge = (function () {
    var c = detectCountry();
    if (c && AGE_BY_COUNTRY[c]) return AGE_BY_COUNTRY[c];
    return DEFAULT_AGE;
  })();

  // ---- i18n ---------------------------------------------------------------
  var STRINGS = {
    en: {
      title:       'Are you <em>{age}</em> or older?',
      body:        'This site contains information about gambling. By law, you must be at least {age} years old to enter.',
      yes:         'Yes, I am {age}+',
      no:          'No, I am under {age}',
      compliance:  '{age}+ · Play responsibly · Gambling can be addictive.',
      leaving:     'Redirecting to responsible gaming resource…',
      brand_tag:   'KOZYR · POKER RAKEBACK'
    },
    ru: {
      title:       'Тебе есть <em>{age}</em>?',
      body:        'Сайт содержит информацию об азартных играх. По закону ты должен быть старше {age} лет, чтобы продолжить.',
      yes:         'Да, мне есть {age}',
      no:          'Мне меньше {age}',
      compliance:  '{age}+ · Играй ответственно · Игры могут вызывать зависимость.',
      leaving:     'Переходим к ресурсу по ответственной игре…',
      brand_tag:   'KOZYR · РЕЙКБЕК В ПОКЕРЕ'
    },
    uk: {
      title:       'Тобі є <em>{age}</em>?',
      body:        'Сайт містить інформацію про азартні ігри. За законом ти повинен бути старшим за {age} років, щоб продовжити.',
      yes:         'Так, мені є {age}',
      no:          'Мені менше {age}',
      compliance:  '{age}+ · Грай відповідально · Ігри можуть викликати залежність.',
      leaving:     'Переходимо до ресурсу з відповідальної гри…',
      brand_tag:   'KOZYR · РЕЙКБЕК У ПОКЕРІ'
    }
  };

  function detectLang() {
    try {
      var saved = localStorage.getItem('kozyr_lang');
      if (saved && STRINGS[saved]) return saved;
    } catch (e) {}
    var tags = [];
    if (navigator.languages) tags = tags.concat(navigator.languages);
    if (navigator.language) tags.push(navigator.language);
    for (var i = 0; i < tags.length; i++) {
      var t = String(tags[i]).toLowerCase();
      if (t.indexOf('uk') === 0) return 'uk';
      if (t.indexOf('ru') === 0) return 'ru';
      if (t.indexOf('be') === 0) return 'ru';
      if (t.indexOf('en') === 0) return 'en';
    }
    return 'en';
  }

  var lang = detectLang();
  var t = STRINGS[lang];

  // Подставляем минимальный возраст ({age} → 21/18/…) во все строки текущего
  // языка. Возраст уже определён выше по стране из URL (minAge).
  (function applyAge() {
    var filled = {};
    for (var key in t) {
      if (t.hasOwnProperty(key)) {
        filled[key] = String(t[key]).replace(/\{age\}/g, minAge);
      }
    }
    t = filled;
  })();

  // ---- Inject styles ------------------------------------------------------
  var STYLE = [
    '@keyframes kzAgeFadeIn { from { opacity: 0; } to { opacity: 1; } }',
    '@keyframes kzAgeSlideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }',

    '.kz-age-overlay {',
    '  position: fixed; inset: 0; z-index: 999999;',
    '  background: rgba(0, 7, 20, 0.85);',
    '  backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);',
    '  display: flex; align-items: center; justify-content: center;',
    '  padding: 20px; overflow-y: auto;',
    '  animation: kzAgeFadeIn 0.25s ease-out;',
    '  font-family: "Space Grotesk", system-ui, sans-serif;',
    '}',

    '.kz-age-card {',
    '  background: #000714;',
    '  border: 1px solid rgba(255,255,255,0.10);',
    '  border-radius: 24px;',
    '  padding: 44px 40px 36px;',
    '  max-width: 480px; width: 100%;',
    '  text-align: center;',
    '  color: #E8ECF7;',
    '  box-shadow: 0 24px 64px rgba(0,0,0,0.5);',
    '  position: relative; overflow: hidden;',
    '  animation: kzAgeSlideUp 0.35s cubic-bezier(.2,.7,.3,1);',
    '}',

    // Radial glow behind title
    '.kz-age-card::before {',
    '  content: ""; position: absolute; inset: 0; pointer-events: none;',
    '  background: radial-gradient(ellipse 400px 200px at 50% 20%, rgba(38,104,255,0.20), transparent 70%);',
    '}',
    '.kz-age-card > * { position: relative; z-index: 1; }',

    '.kz-age-brand {',
    '  display: inline-flex; align-items: center; gap: 8px;',
    '  font-family: "JetBrains Mono", monospace;',
    '  font-size: 11px; font-weight: 500;',
    '  letter-spacing: 0.18em; text-transform: uppercase;',
    '  color: #E4B95B; margin-bottom: 26px;',
    '}',
    '.kz-age-brand::before, .kz-age-brand::after {',
    '  content: ""; width: 24px; height: 1px; background: #E4B95B; opacity: 0.55;',
    '}',

    '.kz-age-title {',
    '  font-family: "Space Grotesk", sans-serif;',
    '  font-weight: 700; font-size: 34px; line-height: 1.1;',
    '  letter-spacing: -0.025em; color: #fff;',
    '  margin: 0 0 16px;',
    '}',
    '.kz-age-title em {',
    '  font-family: "Instrument Serif", Georgia, serif;',
    '  font-style: italic; font-weight: 400;',
    '  font-size: 1.05em; line-height: 1;',
    '  vertical-align: baseline;',
    '  padding: 0 0.03em;',
    '  background: linear-gradient(180deg, #6DA0FF 0%, #2668FF 100%);',
    '  -webkit-background-clip: text; background-clip: text;',
    '  -webkit-text-fill-color: transparent;',
    '  color: transparent;',
    '}',

    '.kz-age-body {',
    '  font-family: "Inter", sans-serif;',
    '  font-size: 15px; line-height: 1.55;',
    '  color: rgba(255,255,255,0.72);',
    '  margin: 0 auto 32px; max-width: 380px;',
    '}',

    '.kz-age-buttons {',
    '  display: flex; flex-direction: column; gap: 10px;',
    '  margin-bottom: 24px;',
    '}',

    '.kz-age-btn {',
    '  display: block; width: 100%;',
    '  padding: 14px 24px;',
    '  border: none; border-radius: 100px;',
    '  font-family: "Space Grotesk", sans-serif;',
    '  font-weight: 600; font-size: 15px;',
    '  letter-spacing: -0.01em; cursor: pointer;',
    '  transition: background 0.15s, transform 0.15s, box-shadow 0.15s;',
    '}',
    '.kz-age-btn:focus { outline: 2px solid #6DA0FF; outline-offset: 2px; }',

    '.kz-age-btn--yes {',
    '  background: #2668FF; color: #fff;',
    '  box-shadow: 0 8px 20px rgba(38,104,255,0.30);',
    '}',
    '.kz-age-btn--yes:hover {',
    '  background: #1E52D9;',
    '  transform: translateY(-1px);',
    '  box-shadow: 0 12px 26px rgba(38,104,255,0.42);',
    '}',

    '.kz-age-btn--no {',
    '  background: transparent; color: rgba(255,255,255,0.55);',
    '  border: 1px solid rgba(255,255,255,0.12);',
    '}',
    '.kz-age-btn--no:hover {',
    '  color: #fff;',
    '  border-color: rgba(255,255,255,0.22);',
    '  background: rgba(255,255,255,0.04);',
    '}',

    '.kz-age-compliance {',
    '  font-family: "JetBrains Mono", monospace;',
    '  font-size: 10px; letter-spacing: 0.06em;',
    '  color: rgba(255,255,255,0.35); line-height: 1.55;',
    '  padding-top: 22px; border-top: 1px solid rgba(255,255,255,0.06);',
    '}',

    // Prevent body scroll while modal is open
    'html.kz-age-locked, html.kz-age-locked body { overflow: hidden !important; }',

    '@media (max-width: 480px) {',
    '  .kz-age-card { padding: 34px 24px 28px; border-radius: 20px; }',
    '  .kz-age-title { font-size: 28px; }',
    '  .kz-age-body { font-size: 14px; }',
    '}'
  ].join('\n');

  function injectStyle() {
    var style = document.createElement('style');
    style.setAttribute('data-kozyr', 'age-gate');
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  // ---- Build & mount modal ------------------------------------------------
  function buildModal() {
    var overlay = document.createElement('div');
    overlay.className = 'kz-age-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'kzAgeTitle');
    overlay.setAttribute('aria-describedby', 'kzAgeBody');

    var card = document.createElement('div');
    card.className = 'kz-age-card';

    // Brand tag
    var brand = document.createElement('div');
    brand.className = 'kz-age-brand';
    brand.textContent = t.brand_tag;
    card.appendChild(brand);

    // Title (with <em> allowed)
    var title = document.createElement('h2');
    title.className = 'kz-age-title';
    title.id = 'kzAgeTitle';
    title.innerHTML = t.title;
    card.appendChild(title);

    // Body
    var body = document.createElement('p');
    body.className = 'kz-age-body';
    body.id = 'kzAgeBody';
    body.textContent = t.body;
    card.appendChild(body);

    // Buttons
    var btnWrap = document.createElement('div');
    btnWrap.className = 'kz-age-buttons';

    var yesBtn = document.createElement('button');
    yesBtn.className = 'kz-age-btn kz-age-btn--yes';
    yesBtn.type = 'button';
    yesBtn.textContent = t.yes;
    yesBtn.addEventListener('click', confirmAge);

    var noBtn = document.createElement('button');
    noBtn.className = 'kz-age-btn kz-age-btn--no';
    noBtn.type = 'button';
    noBtn.textContent = t.no;
    noBtn.addEventListener('click', declineAge);

    btnWrap.appendChild(yesBtn);
    btnWrap.appendChild(noBtn);
    card.appendChild(btnWrap);

    // Compliance footer
    var comp = document.createElement('div');
    comp.className = 'kz-age-compliance';
    comp.textContent = t.compliance;
    card.appendChild(comp);

    overlay.appendChild(card);
    return { overlay: overlay, yesBtn: yesBtn };
  }

  function confirmAge() {
    try {
      localStorage.setItem(STORAGE_KEY, 'true');
      localStorage.setItem(STORAGE_KEY_TS, new Date().toISOString());
    } catch (e) {}
    close();
    // Signal to other scripts (e.g. cookie consent) that age is confirmed
    try {
      window.dispatchEvent(new CustomEvent('kozyr:age-confirmed'));
    } catch (e) {}
  }

  function declineAge() {
    // Show brief loading state before redirect
    var overlay = document.querySelector('.kz-age-overlay');
    if (overlay) {
      overlay.innerHTML = '<div class="kz-age-card"><p class="kz-age-body" style="margin:0;">' + t.leaving + '</p></div>';
    }
    setTimeout(function () {
      window.location.href = REDIRECT_URL;
    }, 400);
  }

  function close() {
    var overlay = document.querySelector('.kz-age-overlay');
    if (overlay) overlay.remove();
    document.documentElement.classList.remove('kz-age-locked');
  }

  // ---- Boot ---------------------------------------------------------------
  function boot() {
    injectStyle();
    var built = buildModal();
    document.body.appendChild(built.overlay);
    document.documentElement.classList.add('kz-age-locked');
    // Focus the yes-button for keyboard navigation
    setTimeout(function () { built.yesBtn.focus(); }, 100);

    // Focus trap: prevent Tab from escaping the modal
    built.overlay.addEventListener('keydown', function (evt) {
      if (evt.key !== 'Tab') return;
      var focusables = built.overlay.querySelectorAll('button, [tabindex]:not([tabindex="-1"])');
      if (!focusables.length) return;
      var first = focusables[0], last = focusables[focusables.length - 1];
      if (evt.shiftKey && document.activeElement === first) {
        evt.preventDefault(); last.focus();
      } else if (!evt.shiftKey && document.activeElement === last) {
        evt.preventDefault(); first.focus();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // ---- Public API (for consent banner and analytics to use) ---------------
  window.KozyrAgeGate = {
    isConfirmed: function () {
      try { return localStorage.getItem(STORAGE_KEY) === 'true'; }
      catch (e) { return false; }
    },
    reset: function () {
      try {
        localStorage.removeItem(STORAGE_KEY);
        localStorage.removeItem(STORAGE_KEY_TS);
      } catch (e) {}
    }
  };
})();
