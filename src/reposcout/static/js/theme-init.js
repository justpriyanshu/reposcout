// Classic, blocking script in <head>: apply the saved theme before first paint so it doesn't flash.
// nav.js's applyTheme() owns the theme after start-up; this only mirrors its first step.
(function () {
  try {
    var chosen = JSON.parse(localStorage.getItem("rs.theme"));
    if (chosen === "light" || chosen === "dark") document.documentElement.dataset.theme = chosen;
  } catch (e) { /* storage blocked or bad value: follow the system */ }
})();
