const _ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => _ESC[c]);
}

function safeUrl(u) {
  const s = String(u ?? "").trim();
  return /^https?:\/\//i.test(s) ? s : "#";
}

function setupFontSize() {
  let fsLevel = parseInt(localStorage.getItem("fontSize") ?? "1");
  if (isNaN(fsLevel) || fsLevel < 0 || fsLevel > 2) fsLevel = 1;

  function applyFs() {
    document.body.className = document.body.className.replace(/\bfs-\d\b/g, "").trim();
    document.body.classList.add("fs-" + fsLevel);
    document.getElementById("font-dec").classList.toggle("disabled", fsLevel === 0);
    document.getElementById("font-inc").classList.toggle("disabled", fsLevel === 2);
  }

  applyFs();
  document.getElementById("font-inc").addEventListener("click", () => {
    if (fsLevel < 2) {
      fsLevel++;
      localStorage.setItem("fontSize", fsLevel);
      applyFs();
    }
  });
  document.getElementById("font-dec").addEventListener("click", () => {
    if (fsLevel > 0) {
      fsLevel--;
      localStorage.setItem("fontSize", fsLevel);
      applyFs();
    }
  });
}

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("sw.js").catch(() => {});
    });
  }
}
