/* ============================================================================
   KOZYR — модуль работы с гео-состоянием
   ---------------------------------------------------------------------------
   Одна точка правды о том, из какой страны зашёл пользователь.
   Используется:
     • plашкой "Принимает вашу страну" на карточках партнёров (finder, pcard)
     • баннером для не-UA посетителей (уже был на главной)
     • Kozyr Match калькулятором (в будущем — подстановка страны)

   API:
     KozyrGeo.get()             → строка 'ua' | 'ru' | 'kz' | ... | null
     KozyrGeo.onReady(fn)       → колбэк, дёргается когда страна известна
                                   (вызывается сразу если уже кэширована)
     KozyrGeo.set(code)         → форсированная установка (например,
                                   если пользователь выбрал регион вручную)

   Хранение: localStorage['kozyr_country'] = 'ua' (в нижнем регистре).
   Источник: /cdn-cgi/trace (Cloudflare), TTL 24 часа. Без стороннего сервиса.
   ==========================================================================*/
(function () {
  'use strict';

  var STORAGE_KEY = 'kozyr_country';
  var STORAGE_TS_KEY = 'kozyr_country_ts';
  var TTL_MS = 24 * 60 * 60 * 1000;  /* 24 часа */

  var state = {
    country: null,   /* нижний регистр, e.g. 'ua' */
    ready: false,
    callbacks: []
  };

  /* ---------- storage ---------------------------------------------- */

  function loadFromCache() {
    try {
      var code = localStorage.getItem(STORAGE_KEY);
      var ts = parseInt(localStorage.getItem(STORAGE_TS_KEY) || '0', 10);
      if (!code) return null;
      if (Date.now() - ts > TTL_MS) return null;
      return code.toLowerCase();
    } catch (e) { return null; }
  }

  function saveToCache(code) {
    try {
      localStorage.setItem(STORAGE_KEY, code.toLowerCase());
      localStorage.setItem(STORAGE_TS_KEY, String(Date.now()));
    } catch (e) {}
  }

  /* ---------- уведомление подписчиков ------------------------------ */

  function markReady(code) {
    state.country = code ? code.toLowerCase() : null;
    state.ready = true;
    state.callbacks.forEach(function (fn) {
      try { fn(state.country); } catch (e) {}
    });
    state.callbacks = [];
    /* сигнал через глобальный CustomEvent — чтобы и старый inline-код
       на странице тоже мог реагировать на приход гео */
    try {
      window.dispatchEvent(new CustomEvent('kozyr:geo', {
        detail: { country: state.country }
      }));
    } catch (e) {}
  }

  /* ---------- определение страны через Cloudflare -------------------
     Источник: /cdn-cgi/trace — встроенный endpoint Cloudflare (сайт на
     Cloudflare Pages). Отдаёт страну по IP на СТОРОНЕ Cloudflare, без
     стороннего сервиса и без утечки IP третьим лицам (в отличие от ipapi.co).
     Ответ — текст вида "...\nloc=UA\n...". Берём loc. */

  function fetchGeo() {
    fetch('/cdn-cgi/trace', { method: 'GET' })
      .then(function (r) {
        if (!r.ok) throw new Error('bad status');
        return r.text();
      }).then(function (text) {
        var code = null;
        var m = /(?:^|\n)loc=([A-Za-z]{2})/.exec(text);
        if (m) code = m[1].toLowerCase();
        if (code) saveToCache(code);
        markReady(code);
      }).catch(function () {
        /* не отдалось — отдаём null, ui не рушится, просто прячет плашку */
        markReady(null);
      });
  }

  /* ---------- public API ------------------------------------------ */

  var KozyrGeo = {
    get: function () { return state.country; },
    isReady: function () { return state.ready; },
    onReady: function (fn) {
      if (typeof fn !== 'function') return;
      if (state.ready) { fn(state.country); return; }
      state.callbacks.push(fn);
    },
    set: function (code) {
      if (!code) return;
      saveToCache(code);
      markReady(code);
    }
  };

  window.KozyrGeo = KozyrGeo;

  /* ---------- boot ------------------------------------------------- */

  var cached = loadFromCache();
  if (cached) {
    /* моментально помечаем ready — UI получает плашку в первом кадре */
    markReady(cached);
  } else {
    /* нет кэша — спрашиваем Cloudflare */
    fetchGeo();
  }

})();
