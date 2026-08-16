/* ============================================================================
   KOZYR — Cookie Consent Banner (GDPR / ePrivacy)
   ----------------------------------------------------------------------------
   Slide-in bar at bottom with Accept all / Reject all / Customize.
   Shown once after age-gate is confirmed, choice saved in localStorage.

   • 3 categories: Necessary (always on), Analytics (opt-in), Marketing (opt-in)
   • Detected language: RU / UK / EN
   • Public API: window.KozyrConsent.hasConsent(category) → boolean
   • Fires event 'kozyr:consent-ready' when user makes choice
   • CSP-safe: no inline styles/scripts, no external resources
   ========================================================================== */
(function () {
  'use strict';

  var STORAGE_KEY = 'kozyr_consent_v1';
  var CURRENT_VERSION = 1;

  // ---- Public API (available immediately) ---------------------------------
  window.KozyrConsent = {
    hasConsent: function (category) {
      try {
        var raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return false;
        var data = JSON.parse(raw);
        return data.categories && data.categories[category] === true;
      } catch (e) { return false; }
    },
    getChoice: function () {
      try {
        var raw = localStorage.getItem(STORAGE_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch (e) { return null; }
    },
    reset: function () {
      try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    },
    reopen: function () {
      // Allow user to change consent from the settings link
      if (!document.querySelector('.kz-consent-bar')) {
        boot(true);
      }
    }
  };

  // ---- Skip conditions ----------------------------------------------------
  function alreadyChosen() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return false;
      var data = JSON.parse(raw);
      return data.version === CURRENT_VERSION;
    } catch (e) { return false; }
  }

  // ---- i18n ---------------------------------------------------------------
  var STRINGS = {
    en: {
      title:            'We value your privacy',
      body:             'We use cookies to make the site work, understand how it is used, and improve it. Choose which categories are OK for you.',
      accept_all:       'Accept all',
      reject_all:       'Reject all',
      customize:        'Customize',
      save:             'Save my choice',
      cat_necessary:    'Necessary',
      cat_necessary_d:  'Required for the site to function (i18n, age-gate). Always on.',
      cat_analytics:    'Analytics',
      cat_analytics_d:  'Anonymous usage stats (Google Analytics) to help us improve.',
      cat_marketing:    'Marketing',
      cat_marketing_d:  'Partner tracking pixels (affiliate attribution).',
      always_on:        'Always on',
      policy_link:      'Privacy policy'
    },
    ru: {
      title:            'Мы уважаем твою приватность',
      body:             'Cookies помогают сайту работать, понимать как его используют и улучшать. Выбери какие категории тебе окей.',
      accept_all:       'Принять все',
      reject_all:       'Отклонить все',
      customize:        'Настроить',
      save:             'Сохранить выбор',
      cat_necessary:    'Необходимые',
      cat_necessary_d:  'Нужны для работы сайта (язык, возрастная проверка). Всегда включены.',
      cat_analytics:    'Аналитика',
      cat_analytics_d:  'Анонимная статистика (Google Analytics), помогает нам улучшать сайт.',
      cat_marketing:    'Маркетинг',
      cat_marketing_d:  'Пиксели партнёров для трекинга переходов по нашим ссылкам.',
      always_on:        'Всегда вкл',
      policy_link:      'Политика приватности'
    },
    uk: {
      title:            'Ми поважаємо твою приватність',
      body:             'Cookies допомагають сайту працювати, розуміти як його використовують і покращувати. Обери які категорії тобі окей.',
      accept_all:       'Прийняти все',
      reject_all:       'Відхилити все',
      customize:        'Налаштувати',
      save:             'Зберегти вибір',
      cat_necessary:    'Необхідні',
      cat_necessary_d:  'Потрібні для роботи сайту (мова, перевірка віку). Завжди увімкнені.',
      cat_analytics:    'Аналітика',
      cat_analytics_d:  'Анонімна статистика (Google Analytics), допомагає покращувати сайт.',
      cat_marketing:    'Маркетинг',
      cat_marketing_d:  'Пікселі партнерів для трекінгу переходів за нашими посиланнями.',
      always_on:        'Завжди увімк',
      policy_link:      'Політика приватності'
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

  // ---- Save & fire event --------------------------------------------------
  function saveChoice(categories) {
    var data = {
      version: CURRENT_VERSION,
      timestamp: new Date().toISOString(),
      categories: categories
    };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch (e) {}
    try {
      window.dispatchEvent(new CustomEvent('kozyr:consent-ready', {
        detail: { categories: categories }
      }));
    } catch (e) {}
    var bar = document.querySelector('.kz-consent-bar');
    if (bar) {
      bar.classList.add('kz-consent-bar--closing');
      setTimeout(function () { bar.remove(); }, 300);
    }
  }

  // ---- Styles -------------------------------------------------------------
  var STYLE = [
    '@keyframes kzConsentSlideUp {',
    '  from { transform: translateY(100%); opacity: 0; }',
    '  to   { transform: translateY(0);    opacity: 1; }',
    '}',
    '@keyframes kzConsentSlideDown {',
    '  from { transform: translateY(0);    opacity: 1; }',
    '  to   { transform: translateY(100%); opacity: 0; }',
    '}',

    '.kz-consent-bar {',
    '  position: fixed; bottom: 20px; left: 20px; right: 20px;',
    '  max-width: 640px; margin: 0 auto;',
    '  z-index: 999998;',
    '  background: #000714;',
    '  border: 1px solid rgba(255,255,255,0.10);',
    '  border-radius: 20px;',
    '  padding: 22px 24px;',
    '  color: #E8ECF7;',
    '  font-family: "Inter", system-ui, sans-serif;',
    '  font-size: 14px; line-height: 1.5;',
    '  box-shadow: 0 20px 50px rgba(0,0,0,0.5);',
    '  animation: kzConsentSlideUp 0.35s cubic-bezier(.2,.7,.3,1);',
    '}',
    '.kz-consent-bar--closing { animation: kzConsentSlideDown 0.3s ease-in forwards; }',

    '.kz-consent-title {',
    '  font-family: "Space Grotesk", sans-serif;',
    '  font-weight: 700; font-size: 17px;',
    '  letter-spacing: -0.015em; color: #fff;',
    '  margin: 0 0 8px;',
    '  display: flex; align-items: center; gap: 8px;',
    '}',
    '.kz-consent-title::before {',
    '  content: "🍪"; font-size: 20px;',
    '}',

    '.kz-consent-body {',
    '  color: rgba(255,255,255,0.68);',
    '  margin: 0 0 18px;',
    '  font-size: 13px;',
    '}',

    '.kz-consent-buttons {',
    '  display: flex; gap: 8px; flex-wrap: wrap;',
    '}',

    '.kz-consent-btn {',
    '  padding: 10px 18px;',
    '  border: none; border-radius: 100px;',
    '  font-family: "Space Grotesk", sans-serif;',
    '  font-weight: 600; font-size: 13px;',
    '  letter-spacing: -0.005em; cursor: pointer;',
    '  transition: background 0.15s, transform 0.15s, border-color 0.15s;',
    '  flex: 1 1 auto; min-width: 100px;',
    '}',
    '.kz-consent-btn:focus { outline: 2px solid #6DA0FF; outline-offset: 2px; }',

    '.kz-consent-btn--accept {',
    '  background: #2668FF; color: #fff;',
    '  box-shadow: 0 4px 14px rgba(38,104,255,0.28);',
    '}',
    '.kz-consent-btn--accept:hover { background: #1E52D9; transform: translateY(-1px); }',

    '.kz-consent-btn--secondary {',
    '  background: transparent; color: rgba(255,255,255,0.62);',
    '  border: 1px solid rgba(255,255,255,0.12);',
    '}',
    '.kz-consent-btn--secondary:hover {',
    '  color: #fff; border-color: rgba(255,255,255,0.24);',
    '  background: rgba(255,255,255,0.04);',
    '}',

    // Customize panel (hidden by default)
    '.kz-consent-details {',
    '  margin-top: 18px;',
    '  padding-top: 18px;',
    '  border-top: 1px solid rgba(255,255,255,0.08);',
    '  display: none;',
    '}',
    '.kz-consent-bar.kz-consent-open .kz-consent-details { display: block; }',

    '.kz-consent-cat {',
    '  display: flex; align-items: flex-start; gap: 12px;',
    '  padding: 12px 0;',
    '}',
    '.kz-consent-cat + .kz-consent-cat { border-top: 1px solid rgba(255,255,255,0.05); }',

    '.kz-consent-cat-info { flex: 1; min-width: 0; }',
    '.kz-consent-cat-title {',
    '  font-family: "Space Grotesk", sans-serif;',
    '  font-weight: 600; font-size: 14px;',
    '  color: #fff; margin: 0 0 2px;',
    '}',
    '.kz-consent-cat-desc {',
    '  font-size: 12px; line-height: 1.5;',
    '  color: rgba(255,255,255,0.55);',
    '  margin: 0;',
    '}',

    // Toggle switch
    '.kz-consent-toggle {',
    '  position: relative; flex-shrink: 0;',
    '  width: 44px; height: 24px;',
    '  background: rgba(255,255,255,0.12);',
    '  border-radius: 100px;',
    '  cursor: pointer;',
    '  transition: background 0.2s;',
    '}',
    '.kz-consent-toggle::after {',
    '  content: ""; position: absolute;',
    '  top: 3px; left: 3px;',
    '  width: 18px; height: 18px;',
    '  background: #fff; border-radius: 50%;',
    '  transition: transform 0.2s cubic-bezier(.2,.7,.3,1);',
    '}',
    '.kz-consent-toggle.on { background: #2668FF; }',
    '.kz-consent-toggle.on::after { transform: translateX(20px); }',
    '.kz-consent-toggle.locked {',
    '  background: rgba(5,166,97,0.55);',
    '  cursor: not-allowed;',
    '}',
    '.kz-consent-toggle.locked::after { transform: translateX(20px); background: #fff; }',
    '.kz-consent-toggle input {',
    '  position: absolute; opacity: 0; width: 100%; height: 100%; cursor: inherit; margin: 0;',
    '}',

    '.kz-consent-cat-locked-note {',
    '  font-family: "JetBrains Mono", monospace;',
    '  font-size: 10px; font-weight: 600;',
    '  color: #05C46B; letter-spacing: 0.06em;',
    '  text-transform: uppercase;',
    '  margin-top: 4px;',
    '}',

    '.kz-consent-policy {',
    '  margin-top: 14px;',
    '  font-size: 11px;',
    '  color: rgba(255,255,255,0.4);',
    '  text-align: center;',
    '}',
    '.kz-consent-policy a {',
    '  color: rgba(255,255,255,0.6);',
    '  border-bottom: 1px dotted rgba(255,255,255,0.2);',
    '  text-decoration: none;',
    '}',
    '.kz-consent-policy a:hover { color: #fff; border-bottom-color: #fff; }',

    '@media (max-width: 560px) {',
    '  .kz-consent-bar { padding: 18px 18px; bottom: 12px; left: 12px; right: 12px; border-radius: 16px; }',
    '  .kz-consent-title { font-size: 15px; }',
    '  .kz-consent-body { font-size: 12px; }',
    '  .kz-consent-buttons { flex-direction: column; }',
    '  .kz-consent-btn { width: 100%; }',
    '}'
  ].join('\n');

  function injectStyle() {
    if (document.querySelector('style[data-kozyr="consent"]')) return;
    var style = document.createElement('style');
    style.setAttribute('data-kozyr', 'consent');
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  // ---- Build banner -------------------------------------------------------
  function buildBanner(t, lang) {
    var bar = document.createElement('div');
    bar.className = 'kz-consent-bar';
    bar.setAttribute('role', 'dialog');
    bar.setAttribute('aria-labelledby', 'kzConsentTitle');
    bar.setAttribute('aria-describedby', 'kzConsentBody');

    // Determine legal page path based on language
    var legalHref = (lang === 'uk') ? '/ua/uk/legal/#privacy' : '/ua/legal/#privacy';

    bar.innerHTML =
      '<h3 class="kz-consent-title" id="kzConsentTitle">' + escapeHtml(t.title) + '</h3>' +
      '<p class="kz-consent-body" id="kzConsentBody">' + escapeHtml(t.body) + '</p>' +
      '<div class="kz-consent-buttons">' +
        '<button type="button" class="kz-consent-btn kz-consent-btn--accept" data-action="accept-all">' + escapeHtml(t.accept_all) + '</button>' +
        '<button type="button" class="kz-consent-btn kz-consent-btn--secondary" data-action="reject-all">' + escapeHtml(t.reject_all) + '</button>' +
        '<button type="button" class="kz-consent-btn kz-consent-btn--secondary" data-action="customize">' + escapeHtml(t.customize) + '</button>' +
      '</div>' +
      '<div class="kz-consent-details" id="kzConsentDetails">' +
        buildCategory('necessary', t.cat_necessary, t.cat_necessary_d, true, true) +
        buildCategory('analytics', t.cat_analytics, t.cat_analytics_d, false, false) +
        buildCategory('marketing', t.cat_marketing, t.cat_marketing_d, false, false) +
        '<div class="kz-consent-buttons" style="margin-top:14px;">' +
          '<button type="button" class="kz-consent-btn kz-consent-btn--accept" data-action="save-custom">' + escapeHtml(t.save) + '</button>' +
        '</div>' +
      '</div>' +
      '<p class="kz-consent-policy"><a href="' + legalHref + '">' + escapeHtml(t.policy_link) + '</a></p>';

    // Wire up handlers
    bar.addEventListener('click', function (e) {
      var target = e.target.closest('[data-action]');
      if (!target) return;
      var action = target.getAttribute('data-action');
      if (action === 'accept-all') {
        saveChoice({ necessary: true, analytics: true, marketing: true });
      } else if (action === 'reject-all') {
        saveChoice({ necessary: true, analytics: false, marketing: false });
      } else if (action === 'customize') {
        bar.classList.add('kz-consent-open');
      } else if (action === 'save-custom') {
        var categories = { necessary: true };
        bar.querySelectorAll('.kz-consent-cat input[type="checkbox"]').forEach(function (cb) {
          categories[cb.getAttribute('data-cat')] = cb.checked;
        });
        saveChoice(categories);
      }
    });

    return bar;
  }

  function buildCategory(key, title, desc, defaultOn, locked) {
    var toggleClass = 'kz-consent-toggle' + (locked ? ' locked on' : (defaultOn ? ' on' : ''));
    var input = locked
      ? '<input type="checkbox" checked disabled data-cat="' + key + '">'
      : '<input type="checkbox"' + (defaultOn ? ' checked' : '') + ' data-cat="' + key + '">';
    return '<div class="kz-consent-cat">' +
             '<div class="kz-consent-cat-info">' +
               '<div class="kz-consent-cat-title">' + escapeHtml(title) + '</div>' +
               '<p class="kz-consent-cat-desc">' + escapeHtml(desc) + '</p>' +
             '</div>' +
             '<label class="' + toggleClass + '" data-toggle="' + key + '">' + input + '</label>' +
           '</div>';
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  // Toggle interaction (visual state sync with checkbox)
  function attachToggleHandlers(bar) {
    bar.querySelectorAll('.kz-consent-toggle').forEach(function (toggle) {
      if (toggle.classList.contains('locked')) return;
      var input = toggle.querySelector('input');
      if (!input) return;
      toggle.addEventListener('click', function (e) {
        // Toggle only if user clicked the toggle wrapper, not the input directly
        if (e.target === input) return;
        e.preventDefault();
        input.checked = !input.checked;
        input.dispatchEvent(new Event('change'));
      });
      input.addEventListener('change', function () {
        toggle.classList.toggle('on', input.checked);
      });
    });
  }

  // ---- Boot ---------------------------------------------------------------
  function boot(force) {
    if (!force && alreadyChosen()) return;

    var lang = detectLang();
    var t = STRINGS[lang];

    injectStyle();
    var banner = buildBanner(t, lang);
    document.body.appendChild(banner);
    attachToggleHandlers(banner);
  }

  function waitForAgeGate() {
    // If age-gate not present on this page (e.g. legal pages), show consent right away
    var hasAgeGateScript = !!document.querySelector('script[src*="kozyr-age-gate"]');

    if (!hasAgeGateScript) {
      setTimeout(boot, 300);
      return;
    }

    // Check if already confirmed
    try {
      if (localStorage.getItem('kozyr_age_confirmed') === 'true') {
        setTimeout(boot, 300);
        return;
      }
    } catch (e) { setTimeout(boot, 300); return; }

    // Wait for the age-gate to fire the confirmation event
    window.addEventListener('kozyr:age-confirmed', function () {
      setTimeout(boot, 300);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', waitForAgeGate);
  } else {
    waitForAgeGate();
  }
})();
