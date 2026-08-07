/* ============================================================================
   KOZYR — премиум-поведение статьи блога.
   Прогресс чтения, липкое оглавление со scrollspy, count-up чисел,
   анимации появления. Подключается тегом <script src="/ua/blog/blog-enhance.js" defer>.
   Всё с страховками: при быстром скролле/без observer контент всё равно виден.
   ========================================================================== */
(function () {
  "use strict";
  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  ready(function () {
    // ---- прогресс чтения ----
    var prog = document.querySelector(".kf-progress");
    if (prog) {
      window.addEventListener("scroll", function () {
        var h = document.documentElement;
        var sc = h.scrollTop / (h.scrollHeight - h.clientHeight || 1);
        prog.style.width = (sc * 100) + "%";
      }, { passive: true });
    }

    // ---- оглавление из H2 (только реальные разделы статьи) ----
    var list = document.getElementById("toc-list");
    var article = document.querySelector(".post-body");
    if (list && article) {
      var heads = Array.prototype.filter.call(
        article.querySelectorAll("h2"),
        function (h) { return !h.closest(".faq-section") && !h.closest(".final-cta"); }
      );
      heads.forEach(function (h, i) {
        if (!h.id) h.id = "sec-" + i;
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.href = "#" + h.id;
        a.textContent = h.textContent.replace(/^[\s\u2660\u2665\u2666\u2663]+/, "").trim();
        a.dataset.t = h.id;
        li.appendChild(a);
        list.appendChild(li);
      });
      var links = Array.prototype.slice.call(list.querySelectorAll("a"));
      if ("IntersectionObserver" in window && links.length) {
        var spy = new IntersectionObserver(function (es) {
          es.forEach(function (e) {
            if (e.isIntersecting) {
              links.forEach(function (l) { l.classList.toggle("active", l.dataset.t === e.target.id); });
            }
          });
        }, { rootMargin: "-80px 0px -70% 0px" });
        heads.forEach(function (h) { spy.observe(h); });
      }
    }

    // ---- count-up чисел ([data-count]) ----
    function countUp(el) {
      if (el.dataset.done) return; el.dataset.done = "1";
      var target = parseFloat(el.dataset.count);
      if (isNaN(target)) return;
      var pre = el.dataset.prefix || "", suf = el.dataset.suffix || "";
      var t0 = performance.now(), dur = 900, fin = pre + target + suf;
      (function step(t) {
        var p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
        el.textContent = pre + Math.round(target * e) + suf;
        if (p < 1) requestAnimationFrame(step); else el.textContent = fin;
      })(t0);
    }
    var nums = document.querySelectorAll("[data-count]");
    if ("IntersectionObserver" in window && nums.length) {
      var cO = new IntersectionObserver(function (es) {
        es.forEach(function (e) { if (e.isIntersecting) { countUp(e.target); cO.unobserve(e.target); } });
      }, { threshold: 0.4 });
      nums.forEach(function (el) { cO.observe(el); });
    }
    setTimeout(function () {
      nums.forEach(function (el) {
        if (!el.dataset.done) { el.dataset.done = "1"; el.textContent = (el.dataset.prefix || "") + el.dataset.count + (el.dataset.suffix || ""); }
      });
    }, 1600);

    // ---- reveal при скролле (со страховкой) ----
    var revEls = Array.prototype.slice.call(document.querySelectorAll(".reveal"));
    if ("IntersectionObserver" in window && revEls.length) {
      var rev = new IntersectionObserver(function (es) {
        es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); rev.unobserve(e.target); } });
      }, { threshold: 0, rootMargin: "0px 0px -8% 0px" });
      revEls.forEach(function (el) { rev.observe(el); });
    }
    setTimeout(function () { revEls.forEach(function (el) { el.classList.add("in"); }); }, 1500);
  });
})();
