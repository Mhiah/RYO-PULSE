// Day/night toggle shared by every page. Runs in <head> so the right theme paints first.
(function () {
  var KEY = "ryo-pulse-theme";
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);

  function current() {
    var t = root.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function label(btn) {
    var next = current() === "dark" ? "day" : "night";
    btn.setAttribute("aria-label", "Switch to " + next + " mode");
    btn.title = "Switch to " + next + " mode (t)";
  }
  function toggle() {
    var next = current() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
    document.querySelectorAll("[data-theme-toggle]").forEach(label);
  }
  window.ryoTheme = { toggle: toggle, current: current };
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      label(btn);
      btn.addEventListener("click", toggle);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "t" && !e.ctrlKey && !e.metaKey && !e.altKey && !(e.target.matches && e.target.matches("input, textarea, select"))) toggle();
    });
  });
})();
