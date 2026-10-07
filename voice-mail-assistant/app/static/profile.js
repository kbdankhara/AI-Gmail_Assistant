/* Profile settings: Gmail accounts, AI mode, voice, privacy and data. */
(function () {
  'use strict';
  var state = JSON.parse(document.getElementById('state').textContent);
  var api = VMA.api;

  function setResult(el, ok, text) {
    el.hidden = false;
    el.className = 'result-line ' + (ok ? 'ok' : 'err');
    el.textContent = text;
  }

  /* ---- Gmail App Password help ---- */
  var helpBackdrop = document.getElementById('gmail-help-backdrop'), helpOpener = null;
  function openHelp(from) { helpOpener = from; helpBackdrop.classList.add('on'); document.getElementById('gmail-help-close').focus(); }
  function closeHelp() { helpBackdrop.classList.remove('on'); if (helpOpener) helpOpener.focus(); }
  document.getElementById('gmail-help-close').addEventListener('click', closeHelp);
  helpBackdrop.addEventListener('click', function (e) { if (e.target === helpBackdrop) closeHelp(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && helpBackdrop.classList.contains('on')) closeHelp(); });

  /* ---- Gmail accounts ---- */
  var list = document.getElementById('gmail-accounts'), rowSeq = 0;
  var TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path></svg>';

  function addRow(account) {
    var n = ++rowSeq, primary = list.children.length === 0;
    var row = document.createElement('div');
    row.className = 'gmail-account';
    row.innerHTML =
      '<div class="account-row-head"><span class="account-label">' + (primary ? 'Primary account' : 'Additional account') + '</span>' +
      (primary ? '' : '<button type="button" class="remove-account" aria-label="Remove this account">' + TRASH + '</button>') + '</div>' +
      '<div class="field"><label for="gm-a' + n + '">Gmail address</label><input id="gm-a' + n + '" type="email" class="gmail-addr-input" placeholder="name@gmail.com" autocomplete="off" /></div>' +
      '<div class="field"><label for="gm-p' + n + '">App Password <span class="saved" hidden>saved ✓</span></label><input id="gm-p' + n + '" type="password" class="gmail-pass-input" placeholder="16-character app password" autocomplete="off" /><span class="hint"><a href="#" class="gmail-help-trigger">How do I create one?</a></span></div>' +
      '<div class="field-row"><button type="button" class="test-btn save gmail-action">Save</button><span class="result-line" role="status" hidden></span></div>';
    list.appendChild(row);
    if (account) {
      row.dataset.id = account.id;
      row.querySelector('.gmail-addr-input').value = account.address;
      if (account.saved) markSaved(row);
    }
    return row;
  }

  function markSaved(row) {
    var pass = row.querySelector('.gmail-pass-input'), btn = row.querySelector('.gmail-action');
    row.querySelector('.saved').hidden = false;
    pass.value = ''; pass.placeholder = 'Leave blank to keep the saved one';
    btn.classList.remove('save'); btn.textContent = 'Test Gmail login'; row.dataset.mode = 'test';
  }
  function markDirty(row) {
    var btn = row.querySelector('.gmail-action');
    btn.classList.add('save'); btn.textContent = 'Save'; row.dataset.mode = 'save';
    row.querySelector('.result-line').hidden = true;
  }

  function saveRow(row) {
    var btn = row.querySelector('.gmail-action'), result = row.querySelector('.result-line');
    var body = { address: row.querySelector('.gmail-addr-input').value, app_password: row.querySelector('.gmail-pass-input').value };
    if (row.dataset.id) body.id = Number(row.dataset.id);
    btn.disabled = true; btn.textContent = 'Saving…'; result.hidden = true;
    return api('POST', '/api/gmail-accounts', body).then(function (r) {
      row.dataset.id = r.account.id;
      row.querySelector('.gmail-addr-input').value = r.account.address;
      btn.disabled = false; markSaved(row);
      document.getElementById('profile-error').hidden = true;
    }, function (e) {
      btn.disabled = false; btn.textContent = 'Save';
      setResult(result, false, e.message);
      throw e;
    });
  }

  function testRow(row) {
    var btn = row.querySelector('.gmail-action'), result = row.querySelector('.result-line');
    btn.disabled = true; btn.textContent = 'Testing…';
    api('POST', '/api/gmail-accounts/' + row.dataset.id + '/test').then(function (r) {
      setResult(result, true, r.message);
    }, function (e) { setResult(result, false, e.message); }).then(function () {
      btn.disabled = false; btn.textContent = 'Test Gmail login';
    });
  }

  list.addEventListener('input', function (e) {
    var row = e.target.closest('.gmail-account');
    if (row && row.dataset.mode === 'test') markDirty(row);
  });
  list.addEventListener('click', function (e) {
    var row = e.target.closest('.gmail-account');
    if (!row) return;
    var help = e.target.closest('.gmail-help-trigger');
    if (help) { e.preventDefault(); openHelp(help); return; }
    if (e.target.closest('.remove-account')) {
      if (!row.dataset.id) { row.remove(); return; }
      if (!confirm('Remove ' + row.querySelector('.gmail-addr-input').value + ' from the assistant?')) return;
      api('DELETE', '/api/gmail-accounts/' + row.dataset.id).then(function () { row.remove(); VMA.toast('Account removed.'); },
        function (err) { setResult(row.querySelector('.result-line'), false, err.message); });
      return;
    }
    if (e.target.closest('.gmail-action')) {
      if (row.dataset.mode === 'test') testRow(row); else saveRow(row).catch(function () {});
    }
  });
  document.getElementById('add-gmail-account').addEventListener('click', function () {
    addRow(null).querySelector('.gmail-addr-input').focus();
  });
  if (state.gmail_accounts.length) state.gmail_accounts.forEach(addRow); else addRow(null);

  /* ---- AI mode ---- */
  var provider = document.getElementById('ai-provider'), model = document.getElementById('ai-model');
  var key = document.getElementById('ai-key'), aiBtn = document.getElementById('ai-action');
  var aiSaved = document.getElementById('ai-saved'), aiResult = document.getElementById('ai-result');
  var saved = {}, aiMode = 'save', aiTouched = false;
  state.ai.saved.forEach(function (p) { saved[p] = true; });

  function aiRefresh(resetModel) {
    var p = provider.value, has = !!saved[p];
    if (resetModel) model.value = state.providers[p].model;
    aiSaved.hidden = !has;
    key.placeholder = has ? 'Leave blank to keep the saved one' : state.providers[p].key_hint;
    aiResult.hidden = true;
    if (has && !aiTouched) { aiBtn.classList.remove('save'); aiBtn.textContent = 'Test AI connection'; aiMode = 'test'; }
    else { aiBtn.classList.add('save'); aiBtn.textContent = 'Save'; aiMode = 'save'; }
  }
  provider.addEventListener('change', function () { key.value = ''; aiTouched = true; aiRefresh(true); });
  model.addEventListener('input', function () { aiTouched = true; aiRefresh(false); });
  key.addEventListener('input', function () { aiTouched = true; aiRefresh(false); });

  function saveAI() {
    aiBtn.disabled = true; aiBtn.textContent = 'Saving…';
    return api('POST', '/api/ai', { provider: provider.value, model: model.value, api_key: key.value }).then(function () {
      saved[provider.value] = true; key.value = ''; aiTouched = false; aiBtn.disabled = false; aiRefresh(false);
    }, function (e) {
      aiBtn.disabled = false; aiBtn.textContent = 'Save';
      setResult(aiResult, false, e.message);
      throw e;
    });
  }
  aiBtn.addEventListener('click', function () {
    if (aiMode === 'save') { saveAI().catch(function () {}); return; }
    aiBtn.disabled = true; aiBtn.textContent = 'Testing…';
    api('POST', '/api/ai/test').then(function (r) { setResult(aiResult, true, r.message); },
      function (e) { setResult(aiResult, false, e.message); }).then(function () {
        aiBtn.disabled = false; aiBtn.textContent = 'Test AI connection';
      });
  });
  aiRefresh(false);

  /* ---- Save settings: saves anything still unsaved above, then continues ---- */
  var saveBtn = document.getElementById('save-profile'), errBox = document.getElementById('profile-error');
  saveBtn.addEventListener('click', function () {
    errBox.hidden = true; saveBtn.disabled = true; saveBtn.textContent = 'Saving…';
    var chain = Promise.resolve();
    Array.prototype.forEach.call(list.children, function (row) {
      var typed = row.querySelector('.gmail-addr-input').value.trim() || row.querySelector('.gmail-pass-input').value.trim();
      if (row.dataset.mode !== 'test' && typed) chain = chain.then(function () { return saveRow(row); });
    });
    if (aiMode === 'save' && (key.value.trim() || saved[provider.value])) chain = chain.then(saveAI);
    chain.then(function () {
      return api('POST', '/api/settings', { voice_lang: document.getElementById('voice-lang').value });
    }).then(function (r) { location.href = r.next; }, function (e) {
      errBox.hidden = false; errBox.querySelector('span').textContent = e.message;
      saveBtn.disabled = false; saveBtn.textContent = 'Save settings';
      errBox.scrollIntoView({ block: 'center' });
    });
  });

  /* ---- Delete account ---- */
  var delInput = document.getElementById('delete-confirm'), delBtn = document.getElementById('delete-account');
  if (delInput) {
    delInput.addEventListener('input', function () { delBtn.disabled = delInput.value !== 'DELETE'; });
    delBtn.addEventListener('click', function () {
      delBtn.disabled = true; delBtn.textContent = 'Deleting…';
      api('POST', '/api/me/delete', { confirm: delInput.value }).then(function () { location.href = '/'; }, function (e) {
        setResult(document.getElementById('delete-result'), false, e.message);
        delBtn.disabled = false; delBtn.textContent = 'Delete my account';
      });
    });
  }
})();
