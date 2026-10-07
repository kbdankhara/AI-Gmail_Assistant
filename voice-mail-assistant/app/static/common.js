/* Shared helpers: API calls with CSRF, account drawer, sign out, toast. */
(function () {
  'use strict';
  var csrf = document.querySelector('meta[name="csrf-token"]').content;

  function api(method, url, body) {
    var opts = { method: method, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf, 'Accept': 'application/json' } };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) {
          var err = new Error(data.error || 'Something went wrong. Please try again.');
          err.status = resp.status;
          throw err;
        }
        return data;
      });
    }, function () {
      var err = new Error("Couldn't reach the server. Check your connection.");
      err.status = 0;
      throw err;
    });
  }

  var toastEl = document.getElementById('toast'), toastTimer;
  function toast(text) {
    toastEl.textContent = text;
    toastEl.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('on'); }, 3200);
  }

  function signOut() {
    document.dispatchEvent(new CustomEvent('vma:signout'));
    api('POST', '/auth/signout').catch(function () {}).then(function () { location.href = '/login'; });
  }
  Array.prototype.forEach.call(document.querySelectorAll('[data-signout]'), function (el) {
    el.addEventListener('click', signOut);
  });

  var drawer = document.getElementById('drawer'), backdrop = document.getElementById('backdrop');
  var avatar = document.getElementById('nav-avatar');
  if (drawer && avatar) {
    var open = function () { backdrop.classList.add('on'); drawer.classList.add('on'); drawer.inert = false; document.getElementById('drawer-close').focus(); };
    var close = function () { backdrop.classList.remove('on'); drawer.classList.remove('on'); drawer.inert = true; };
    avatar.addEventListener('click', open);
    backdrop.addEventListener('click', close);
    document.getElementById('drawer-close').addEventListener('click', function () { close(); avatar.focus(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && drawer.classList.contains('on')) { close(); avatar.focus(); } });
  }

  window.VMA = { api: api, toast: toast, signOut: signOut };
})();
