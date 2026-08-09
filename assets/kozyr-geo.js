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
   Источник: fetch к ipapi.co/json/, TTL 24 часа.
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

  /* ---------- fetch до ipapi -------------------------------------- */

  function fetchGeo() {
    fetch('https://ipapi.co/json/', {
      method: 'GET',
      headers: { 'Accept': 'application/json' }
    }).then(function (r) {
      if (!r.ok) throw new Error('bad status');
      return r.json();
    }).then(function (data) {
      var code = data && data.country_code ? String(data.country_code).toLowerCase() : null;
      if (code) saveToCache(code);
      markReady(code);
    }).catch(function () {
      /* сеть не отдала — отдаём null, ui не рушится, просто прячет плашку */
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
    /* нет кэша — ждём ответа ipapi */
    fetchGeo();
  }

})();
