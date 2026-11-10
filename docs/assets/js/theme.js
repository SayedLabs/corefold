/* Runs before the page is drawn, so a chosen look (light or dark) never flashes the other one first.
   The choice is kept only in this browser (localStorage); nothing is sent anywhere. */
(function () {
  var d = document.documentElement, c = null;
  try { c = localStorage.getItem("rk-theme"); } catch (e) {}
  if (c === "light" || c === "dark") { d.setAttribute("data-theme", c); d.setAttribute("data-theme-choice", c); }
  d.classList.add("js");
  /* First visit to the English home page from a browser set to Arabic: the Arabic site. Once someone picks a
     language with the switch, that choice wins. */
  try {
    var lang = localStorage.getItem("rk-lang");
    var b = d.getAttribute("data-home-en");
    if (b !== null && !lang && /^ar\b/i.test((navigator.languages && navigator.languages[0]) || navigator.language || "")) {
      location.replace(b + "ar/");
    }
  } catch (e) {}
})();
