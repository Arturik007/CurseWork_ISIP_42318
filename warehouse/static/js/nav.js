(function () {
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.querySelector("[data-nav-toggle]");
    var nav = document.querySelector("[data-nav-main]");
    if (btn && nav) {
      btn.addEventListener("click", function () {
        nav.classList.toggle("is-open");
      });
    }
  });
})();
