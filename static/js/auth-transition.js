(function () {
  "use strict";

  // Swaps between the Login and Register screens entirely client-side
  // instead of doing a real navigation: the other page's HTML is
  // fetched and cached the moment this page finishes loading, so by
  // the time someone taps the other tab, there's nothing left to wait
  // on -- just a DOM swap plus the shell's existing fade. Falls back
  // to a normal link (full navigation) for anything that goes wrong,
  // so a fetch failure never leaves the tab looking broken.
  var shell = document.querySelector(".auth-shell");
  var view = document.getElementById("auth-view");
  var overlaySlot = document.getElementById("auth-overlay-slot");
  if (!shell || !view || !overlaySlot) return;

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var cache = {}; // href -> Promise<{ viewHtml, overlayHtml, title, page }>

  function parseEntry(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var freshView = doc.getElementById("auth-view");
    var freshOverlay = doc.getElementById("auth-overlay-slot");
    var freshShell = doc.querySelector(".auth-shell");
    if (!freshView || !freshOverlay || !freshShell) throw new Error("unexpected page shape");
    return {
      viewHtml: freshView.innerHTML,
      overlayHtml: freshOverlay.innerHTML,
      title: doc.title,
      page: freshShell.getAttribute("data-auth-page"),
    };
  }

  function prefetch(href) {
    if (!href || cache[href]) return;
    cache[href] = fetch(href, { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("bad response");
        return res.text();
      })
      .then(parseEntry)
      .catch(function (err) {
        delete cache[href];
        throw err;
      });
  }

  function prefetchTabs() {
    var links = view.querySelectorAll(".auth-tabs a.auth-tab[href]");
    Array.prototype.forEach.call(links, function (a) {
      prefetch(a.getAttribute("href"));
    });
  }

  function runPageInit(page) {
    if (page === "signup" && window.initSignupPage) window.initSignupPage();
    if (page === "login" && window.initInstallPrompt) window.initInstallPrompt();
    if (page === "login" && window.initLoginPage) window.initLoginPage();
  }

  function applyEntry(entry, href, pushState) {
    view.innerHTML = entry.viewHtml;
    overlaySlot.innerHTML = entry.overlayHtml;
    document.title = entry.title;
    shell.setAttribute("data-auth-page", entry.page);
    if (pushState) history.pushState({ authSwap: true }, "", href);
    shell.classList.remove("is-leaving");
    runPageInit(entry.page);
    prefetchTabs();
  }

  function swapTo(href, pushState) {
    prefetch(href);
    var pending = cache[href];

    function finish(entry) {
      applyEntry(entry, href, pushState);
    }

    if (reduceMotion) {
      pending.then(finish).catch(function () { window.location.href = href; });
      return;
    }

    shell.classList.add("is-leaving");
    var minFade = new Promise(function (resolve) { setTimeout(resolve, 150); });
    Promise.all([pending, minFade])
      .then(function (results) { finish(results[0]); })
      .catch(function () { window.location.href = href; });
  }

  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest(".auth-tabs a.auth-tab");
    if (!link || !view.contains(link)) return;
    var href = link.getAttribute("href");
    if (!href) return;
    e.preventDefault();
    swapTo(href, true);
  });

  window.addEventListener("popstate", function () {
    swapTo(location.pathname + location.search, false);
  });

  prefetchTabs();
})();
