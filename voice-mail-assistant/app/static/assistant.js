/* The assistant: a voice conversation over the user's unread Gmail.
   Speech recognition and synthesis both run in the browser (Chrome / Edge). */
(function () {
  'use strict';
  var state = JSON.parse(document.getElementById('state').textContent);
  var api = VMA.api;

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var synth = window.speechSynthesis;
  // Demo mode accepts a scripted conversation (used by the automated browser test).
  var script = state.demo && Array.isArray(window.__vmaScript) ? window.__vmaScript : null;
  var supported = !!script || (!!SR && !!synth && window.isSecureContext);

  var POLL_MS = 30000;
  var power = document.getElementById('power'), powerLabel = document.getElementById('power-label'), ring = document.getElementById('ring');
  var pill = document.getElementById('pill'), pillText = document.getElementById('pill-text');
  var logEl = document.getElementById('log'), logEmpty = document.getElementById('log-empty');
  var statUnread = document.getElementById('stat-unread'), statChecked = document.getElementById('stat-checked');
  var liveWrap = document.getElementById('live-indicator'), liveText = document.getElementById('live-text');

  var on = false, runId = 0, rec = null, wakeLock = null, sleepTimer = null, sleepWake = null;
  var seen = {}, unread = 0;

  /* ---------------- what the user can say ---------------- */
  var RX = {
    off: /\b(turn (it |yourself )?off|switch off|shut down|stop listening|good ?bye|that's all|that is all)\b/i,
    yes: /\b(yes|yeah|yep|yup|sure|ok|okay|please|go ahead|go on|do it|send it|send|read (it|them)|absolutely|of course)\b/i,
    no: /\b(no|nope|nah|not now|don't|do not|later|cancel|never mind)\b/i,
    reply: /\b(reply|respond|answer|write back)\b/i,
    mark: /\bmark\b|\bas read\b/i,
    skip: /\b(skip|next|leave it|ignore|move on|pass)\b/i,
    repeat: /\b(repeat|again|once more|say that)\b/i,
    redo: /\b(change|redo|different|start over|try again|rewrite)\b/i,
    check: /\b(check|any new|new (mail|email))\b/i,
    low: /\b(not (so |very |too )?(good|great|well)|bad|terrible|awful|sad|tired|sick|ill|unwell|stressed|rough|exhausted)\b/i
  };

  function Stop() {}                       // the session was switched off mid-step
  function TurnOff() {}                    // the user said "turn off"
  function Fatal(message) { this.message = message; }

  /* ---------------- display ---------------- */
  function fmtTime(d) { var h = d.getHours(), m = d.getMinutes(), ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12; return h + ':' + (m < 10 ? '0' : '') + m + ' ' + ap; }
  function setPill(kind, text) { pill.className = 'pill ' + kind; pillText.textContent = text; }
  function setUnread(n) { unread = Math.max(0, n); statUnread.textContent = unread; }
  function setPowerVisual(isOn) {
    power.classList.toggle('on', isOn); ring.classList.toggle('pulsing', isOn);
    powerLabel.textContent = isOn ? 'Turn off' : 'Turn on';
    power.setAttribute('aria-pressed', String(isOn));
    liveWrap.classList.toggle('active', isOn); liveText.textContent = isOn ? 'on air' : 'idle';
  }
  function addLog(who, text) {
    logEmpty.hidden = true;
    var row = document.createElement('div'); row.className = 'row ' + who;
    var label = document.createElement('div'); label.className = 'who';
    label.textContent = who === 'assistant' ? 'Assistant' : who === 'you' ? 'You' : 'Notice';
    var msg = document.createElement('div'); msg.className = 'msg'; msg.textContent = text;
    var ts = document.createElement('span'); ts.className = 'ts'; ts.textContent = fmtTime(new Date());
    msg.appendChild(ts); row.appendChild(label); row.appendChild(msg);
    logEl.appendChild(row); logEl.scrollTop = logEl.scrollHeight;
  }

  /* ---------------- speaking ---------------- */
  var voice = null, keep = [];
  function pickVoice() {
    var voices = synth ? synth.getVoices() : [];
    voice = voices.filter(function (v) { return v.lang.replace('_', '-') === state.voice_lang; })[0] ||
            voices.filter(function (v) { return v.lang.slice(0, 2) === 'en'; })[0] || null;
  }
  if (synth) { pickVoice(); synth.addEventListener && synth.addEventListener('voiceschanged', pickVoice); }

  function alive(id) { if (id !== runId) throw new Stop(); }

  function utter(text) {
    return new Promise(function (resolve) {
      var u = new SpeechSynthesisUtterance(text), done = false;
      function finish() { if (done) return; done = true; clearTimeout(guard); keep = keep.filter(function (k) { return k !== u; }); resolve(); }
      // Chrome sometimes never fires "end"; don't let the conversation hang on it.
      var guard = setTimeout(finish, 6000 + text.length * 110);
      u.lang = state.voice_lang; if (voice) u.voice = voice;
      u.onend = finish; u.onerror = finish;
      keep.push(u); synth.resume(); synth.speak(u);
    });
  }

  async function speak(id, text, spoken) {
    alive(id);
    setPill('speak', 'Speaking…');
    addLog('assistant', text);
    if (script) { await new Promise(function (r) { setTimeout(r, 20); }); alive(id); return; }
    // Short chunks: Chrome cuts off long utterances.
    var parts = (spoken || text).replace(/\s+/g, ' ').match(/[^.!?]+[.!?]*\s*/g) || [];
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].trim()) { await utter(parts[i].trim()); alive(id); }
    }
  }

  /* ---------------- listening ---------------- */
  function stopRec() { if (rec) { try { rec.onend = null; rec.abort(); } catch (e) {} rec = null; } }

  function hear(opts) {
    opts = opts || {};
    if (script) return new Promise(function (r) { setTimeout(function () { r(script.length ? script.shift() : ''); }, 20); });
    return new Promise(function (resolve, reject) {
      var r = new SR(), finals = [], settled = false, quiet = null;
      var limit = setTimeout(function () { try { r.stop(); } catch (e) {} }, opts.dictation ? 60000 : 12000);
      function settle(fn, value) { if (settled) return; settled = true; clearTimeout(limit); clearTimeout(quiet); if (rec === r) rec = null; fn(value); }
      r.lang = state.voice_lang; r.interimResults = false; r.maxAlternatives = 1;
      r.continuous = !!opts.dictation;
      r.onresult = function (e) {
        for (var i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) finals.push(e.results[i][0].transcript.trim());
        if (opts.dictation) { clearTimeout(quiet); quiet = setTimeout(function () { try { r.stop(); } catch (e2) {} }, 2200); }
      };
      r.onerror = function (e) {
        if (e.error === 'not-allowed' || e.error === 'service-not-allowed') settle(reject, new Fatal('The microphone is blocked. Select the lock or microphone icon in the address bar, allow the microphone for this site, then turn the assistant on again.'));
        else if (e.error === 'audio-capture') settle(reject, new Fatal("No microphone was found. Connect one and turn the assistant on again."));
        else if (e.error === 'network') settle(reject, new Fatal("Speech recognition couldn't reach its service. Check your connection and turn the assistant on again."));
      };
      r.onend = function () { settle(resolve, finals.join(' ').trim()); };
      rec = r;
      try { r.start(); } catch (e) { settle(resolve, ''); }
    });
  }

  async function listen(id, opts) {
    alive(id);
    setPill('listen', 'Listening…');
    var text = await hear(opts);
    alive(id);
    if (text) addLog('you', text);
    if (text && RX.off.test(text) && !(opts && opts.dictation)) throw new TurnOff();
    return text;
  }

  async function ask(id, question, opts) {
    await speak(id, question);
    var text = await listen(id, opts);
    if (!text) {
      await speak(id, "Sorry, I didn't catch that. " + question);
      text = await listen(id, opts);
    }
    return text;
  }

  /* ---------------- talking to the server ---------------- */
  async function call(id, method, url, body) {
    var result;
    try { result = await api(method, url, body); }
    catch (e) {
      alive(id);
      if (e.status === 401) { location.href = '/login'; throw new Stop(); }
      throw new Fatal(e.message);
    }
    alive(id);
    return result;
  }

  function key(e) { return e.account_id + ':' + e.uid; }

  async function checkInbox(id) {
    setPill('check', 'Checking your inbox…');
    var r = await call(id, 'GET', '/api/inbox');
    statChecked.textContent = fmtTime(new Date());
    setUnread(r.emails.length);
    (r.errors || []).forEach(function (x) { addLog('notice', x.address + ': ' + x.error); });
    r.multi = r.accounts > 1;
    return r;
  }

  /* ---------------- the conversation ---------------- */
  async function replyTo(id, mail) {
    for (var attempt = 0; attempt < 3; attempt++) {
      var said = await ask(id, 'What would you like to say in your reply?', { dictation: true });
      if (!said) { await speak(id, "I didn't hear a reply, so I've left the email unread."); return; }
      setPill('check', 'Drafting your reply…');
      var d = await call(id, 'POST', '/api/email/draft', { account_id: mail.account_id, uid: mail.uid, instruction: said });
      if (d.notice) addLog('notice', d.notice + ' Using your words as dictated.');
      await speak(id, 'Here is the reply. ' + d.draft, 'Here is the reply. ' + d.draft.replace(/\n+/g, '. '));
      var answer = await ask(id, 'Shall I send it?');
      if (RX.redo.test(answer)) continue;
      if (RX.yes.test(answer) && !RX.no.test(answer)) {
        setPill('check', 'Sending…');
        var sent = await call(id, 'POST', '/api/email/send', { account_id: mail.account_id, uid: mail.uid, body: d.draft, approved: true });
        setUnread(unread - 1);
        await speak(id, sent.marked_read ? 'Your reply has been sent and the email is marked as read.' : 'Your reply has been sent.');
        return;
      }
      await speak(id, "Okay, I haven't sent it. The email is left unread.");
      return;
    }
    await speak(id, "Let's leave that one for now. Nothing was sent.");
  }

  async function readAll(id, emails, multi) {
    for (var i = 0; i < emails.length; i++) {
      var mail = emails[i];
      setPill('check', 'Reading your email…');
      var s = await call(id, 'POST', '/api/email/summary', { account_id: mail.account_id, uid: mail.uid });
      if (s.notice) addLog('notice', s.notice + ' Reading the start of the email instead.');
      var intro = 'Email ' + (i + 1) + ' of ' + emails.length + '. ' + (multi ? 'In ' + mail.account + '. ' : '') +
                  'From ' + mail.from + '. ' + s.summary;
      await speak(id, intro);
      for (var tries = 0; ; tries++) {
        var choice = await ask(id, 'Would you like to reply, mark it as read, or skip it?');
        if (RX.reply.test(choice)) { await replyTo(id, mail); break; }
        if (RX.mark.test(choice)) {
          await call(id, 'POST', '/api/email/mark-read', { account_id: mail.account_id, uid: mail.uid });
          setUnread(unread - 1);
          await speak(id, "Done. It's marked as read.");
          break;
        }
        if (RX.repeat.test(choice) && tries < 3) { await speak(id, intro); continue; }
        if (!choice || RX.skip.test(choice) || RX.no.test(choice) || tries >= 2) { await speak(id, "Okay, I've left it unread."); break; }
        await speak(id, 'You can say reply, mark as read, or skip.');
      }
    }
    await speak(id, emails.length > 1 ? "That's all of them. I'll keep watching your inbox." : "I'll keep watching your inbox.");
  }

  function sleep(ms) {
    return new Promise(function (resolve) { sleepWake = resolve; sleepTimer = setTimeout(resolve, ms); });
  }
  function wake() { clearTimeout(sleepTimer); if (sleepWake) sleepWake(); }

  /* While watching, keep an ear open for "turn off" or "check my mail". */
  function backgroundEar(id) {
    if (script || !SR) return;
    var r = new SR(), restart = true;
    r.lang = state.voice_lang; r.continuous = true; r.interimResults = false;
    r.onresult = function (e) {
      var text = e.results[e.results.length - 1][0].transcript.trim();
      if (RX.off.test(text)) { addLog('you', text); restart = false; goOff(true); }
      else if (RX.check.test(text)) { addLog('you', text); wake(); }
    };
    r.onerror = function (e) { if (e.error !== 'no-speech' && e.error !== 'aborted') restart = false; };
    r.onend = function () { if (restart && id === runId && rec === r) setTimeout(function () { if (id === runId && rec === r) { try { r.start(); } catch (e) {} } }, 400); };
    rec = r;
    try { r.start(); } catch (e) {}
  }

  async function watch(id) {
    for (;;) {
      alive(id);
      setPill('watch', 'On — watching your inbox');
      backgroundEar(id);
      await sleep(script ? 50 : POLL_MS);
      stopRec();
      alive(id);
      if (script && !script.length) return;   // scripted test run is finished
      var r = await checkInbox(id);
      var fresh = r.emails.filter(function (e) { return !seen[key(e)]; });
      if (!fresh.length) continue;
      fresh.forEach(function (e) { seen[key(e)] = true; });
      var answer = await ask(id, fresh.length === 1 ? 'You have a new email. Shall I read it to you?' : 'You have ' + fresh.length + ' new emails. Shall I read them to you?');
      if (RX.yes.test(answer) && !RX.no.test(answer)) await readAll(id, fresh, r.multi);
      else await speak(id, "Okay. I'll keep watching.");
    }
  }

  async function run(id) {
    await speak(id, 'Hello ' + state.user.first_name + '! How are you doing today?');
    var mood = await listen(id);
    if (mood) await speak(id, RX.low.test(mood) && !/\bnot (too |so |that )?bad\b/i.test(mood) ? "I'm sorry to hear that. I hope the rest of your day gets better." : 'Glad to hear it!');
    var r = await checkInbox(id);
    r.emails.forEach(function (e) { seen[key(e)] = true; });
    if (!r.emails.length) {
      await speak(id, "You have no unread emails. I'll keep watching your inbox.");
    } else {
      var n = r.emails.length;
      var answer = await ask(id, 'You have ' + n + ' unread email' + (n === 1 ? '' : 's') + '. Would you like me to go through them?');
      if (RX.yes.test(answer) && !RX.no.test(answer)) await readAll(id, r.emails, r.multi);
      else await speak(id, "Okay. I'll keep watching your inbox.");
    }
    await watch(id);
  }

  /* ---------------- on and off ---------------- */
  function releaseAll() {
    stopRec(); wake();
    if (synth) synth.cancel();
    if (wakeLock) { try { wakeLock.release(); } catch (e) {} wakeLock = null; }
  }

  function goOff(sayGoodbye) {
    if (!on) return;
    on = false; runId++;
    releaseAll();
    if (sayGoodbye) {
      addLog('assistant', 'Turning off. Goodbye!');
      if (synth && !script) { var u = new SpeechSynthesisUtterance('Turning off. Goodbye!'); u.lang = state.voice_lang; if (voice) u.voice = voice; synth.speak(u); }
    }
    setPowerVisual(false); setPill('off', 'Assistant is off');
  }

  function goOn() {
    on = true; seen = {};
    var id = ++runId;
    setPowerVisual(true);
    if (navigator.wakeLock) navigator.wakeLock.request('screen').then(function (l) { if (id === runId) wakeLock = l; else l.release(); }, function () {});
    run(id).catch(function (e) {
      if (e instanceof Stop) return;
      if (e instanceof TurnOff) { goOff(true); return; }
      addLog('notice', e instanceof Fatal ? e.message : 'Something went wrong, so the assistant turned off. Please try again.');
      if (!(e instanceof Fatal) && window.console) console.error(e);
      goOff(false);
    });
  }

  power.addEventListener('click', function () { if (on) goOff(true); else goOn(); });
  window.addEventListener('pagehide', releaseAll);
  document.addEventListener('vma:signout', function () { goOff(false); });

  if (!supported) { document.getElementById('support-notice').hidden = false; power.disabled = true; }
  if (!state.setup.gmail || !state.billing.access) power.disabled = true;
})();
