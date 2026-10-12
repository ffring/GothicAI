// The short on a page: a muted loop plays unless the reader asked for less motion (with a button to stop
// it), a YouTube video loads only when it is asked for.
(function () {
  var ru = document.documentElement.lang === "ru";
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  document.querySelectorAll("video.loop").forEach(function (video) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "toggle";
    function label() {
      button.textContent = video.paused ? (ru ? "Пустить" : "Play") : (ru ? "Пауза" : "Pause");
    }
    button.addEventListener("click", function () {
      if (video.paused) video.play(); else video.pause();
    });
    video.addEventListener("play", label);
    video.addEventListener("pause", label);
    video.parentNode.appendChild(button);
    label();
    if (!still) {
      var p = video.play();
      if (p && p.catch) p.catch(label);
    }
  });

  document.querySelectorAll("button.yt").forEach(function (button) {
    button.addEventListener("click", function () {
      var frame = document.createElement("iframe");
      frame.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(button.dataset.id) +
        "?autoplay=1&playsinline=1&rel=0";
      frame.title = button.getAttribute("aria-label") || "YouTube";
      frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
      frame.allowFullscreen = true;
      button.replaceWith(frame);
      frame.focus();
    });
  });
})();
