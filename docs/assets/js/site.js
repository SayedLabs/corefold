/* Rakiza Labs website: small touches only. Every page works without it. */
(function () {
  "use strict";
  var d = document.documentElement;
  var head = document.querySelector(".site-head");

  /* the header gets a line under it once the page scrolls */
  function onScroll() { if (head) head.classList.toggle("scrolled", window.scrollY > 4); }
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  /* the menu on small screens */
  var menu = document.querySelector(".menu-btn");
  if (menu && head) {
    menu.addEventListener("click", function () {
      var open = head.classList.toggle("open");
      menu.setAttribute("aria-expanded", String(open));
    });
    head.querySelectorAll(".nav a").forEach(function (a) {
      a.addEventListener("click", function () { head.classList.remove("open"); menu.setAttribute("aria-expanded", "false"); });
    });
  }

  /* light / dark / follow the system, kept in this browser only */
  var mq = window.matchMedia("(prefers-color-scheme: dark)");
  function applyPictures() {
    var t = d.getAttribute("data-theme");
    document.querySelectorAll("picture source[data-dark]").forEach(function (s) {
      s.media = t === "dark" ? "all" : t === "light" ? "not all" : "(prefers-color-scheme: dark)";
    });
  }
  function setTheme(choice) {
    if (choice === "light" || choice === "dark") {
      d.setAttribute("data-theme", choice); d.setAttribute("data-theme-choice", choice);
    } else {
      d.removeAttribute("data-theme"); d.removeAttribute("data-theme-choice");
    }
    try { if (choice) localStorage.setItem("rk-theme", choice); else localStorage.removeItem("rk-theme"); } catch (e) {}
    applyPictures();
    var b = document.querySelector(".theme-btn");
    if (b) b.setAttribute("aria-label", b.getAttribute("data-label-" + (choice || "auto")) || b.getAttribute("aria-label"));
  }
  var tb = document.querySelector(".theme-btn");
  if (tb) {
    tb.addEventListener("click", function () {
      var c = d.getAttribute("data-theme-choice");
      var sysDark = mq.matches;
      /* cycle: system -> the opposite of the system -> the same as the system -> system */
      var next = !c ? (sysDark ? "light" : "dark") : (c === (sysDark ? "light" : "dark") ? (sysDark ? "dark" : "light") : null);
      setTheme(next);
    });
    setTheme(d.getAttribute("data-theme-choice"));
  }
  applyPictures();

  /* remember the language someone picks */
  document.querySelectorAll("[data-set-lang]").forEach(function (a) {
    a.addEventListener("click", function () { try { localStorage.setItem("rk-lang", a.getAttribute("data-set-lang")); } catch (e) {} });
  });

  /* gentle reveal while scrolling */
  if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    document.querySelectorAll(".reveal").forEach(function (el) { io.observe(el); });
  } else {
    document.querySelectorAll(".reveal").forEach(function (el) { el.classList.add("in"); });
  }

  /* Download buttons: ask GitHub (which hosts this site and the app's releases) for the newest version, so the
     button starts the right installer and shows its version and size. Without an answer the button opens the
     releases page, which always has the newest version. */
  var dls = document.querySelectorAll("[data-download]");
  if (dls.length && window.fetch) {
    var ar = d.lang === "ar";
    fetch("https://api.github.com/repos/SayedLabs/corefold/releases/latest", { headers: { "Accept": "application/vnd.github+json" }, credentials: "omit", referrerPolicy: "no-referrer" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rel) {
        if (!rel || !rel.assets) return;
        var ver = String(rel.tag_name || "").replace(/^v/, "");
        var exe = null, zip = null, sums = null;
        rel.assets.forEach(function (a) {
          if (/^RakizaCore-Setup-[\d.]+\.exe$/i.test(a.name)) exe = a;
          else if (/^RakizaCore-[\d.]+-portable\.zip$/i.test(a.name)) zip = a;
          else if (/^SHA256SUMS\.txt$/i.test(a.name)) sums = a;
        });
        if (!exe) return;
        var mb = Math.round(exe.size / 1048576);
        dls.forEach(function (a) { a.href = exe.browser_download_url; });
        document.querySelectorAll("[data-dl-version]").forEach(function (el) {
          el.textContent = ar ? ("الإصدار " + ver + " · " + mb + " ميجابايت") : ("Version " + ver + " · " + mb + " MB");
        });
        document.querySelectorAll("[data-dl-zip]").forEach(function (el) { if (zip) el.href = zip.browser_download_url; });
        document.querySelectorAll("[data-dl-sums]").forEach(function (el) { if (sums) el.href = sums.browser_download_url; });
      })
      .catch(function () {});
  }

  /* the year in the footer */
  document.querySelectorAll("[data-year]").forEach(function (el) { el.textContent = String(new Date().getFullYear()); });
})();
