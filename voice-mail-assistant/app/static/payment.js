/* Payment: free trial, then a monthly Razorpay subscription. */
(function () {
  'use strict';
  var state = JSON.parse(document.getElementById('state').textContent);
  var billing = state.billing, api = VMA.api;
  var badge = document.getElementById('trial-badge'), note = document.getElementById('trial-note');
  var per = document.getElementById('price-per'), btn = document.getElementById('trial-btn');
  var cancelBtn = document.getElementById('cancel-btn'), errBox = document.getElementById('pay-error');

  function day(iso) { return iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : ''; }
  function showError(text) { errBox.hidden = false; errBox.querySelector('span').textContent = text; }

  function render() {
    var s = billing.status, active = false;
    errBox.hidden = true; cancelBtn.hidden = true; btn.hidden = false; btn.disabled = false;
    per.textContent = '/ month';
    if (!billing.enforced) {
      badge.textContent = 'Not switched on';
      note.textContent = "Payments aren't switched on for this deployment yet, so the assistant is free to use.";
      btn.textContent = 'Start free trial'; btn.disabled = true;
    } else if (s === 'trialing' && billing.access) {
      active = true; badge.textContent = 'Trial active';
      note.textContent = 'Your trial ends ' + day(billing.trial_end) + '. ' + state.price + '.00 will be charged then unless you cancel.';
      btn.hidden = true; cancelBtn.hidden = false;
    } else if (s === 'active') {
      active = true; badge.textContent = 'Plan active';
      note.textContent = billing.access_until ? 'Your next payment is on ' + day(billing.access_until) + '.' : 'Your plan is active.';
      btn.hidden = true; cancelBtn.hidden = false;
    } else if (s === 'past_due') {
      badge.textContent = 'Payment failed';
      note.textContent = 'Your last payment did not go through. Razorpay will retry and has emailed you a link to update your payment method.';
      btn.hidden = true; cancelBtn.hidden = false;
    } else if (s === 'cancelled' && billing.access) {
      badge.textContent = 'Cancelled';
      note.textContent = 'Your plan is cancelled. You can keep using the assistant until ' + day(billing.access_until) + '.';
      btn.textContent = 'Subscribe again';
    } else if (billing.trial_available) {
      badge.textContent = state.trial_days + '-day free trial';
      per.textContent = '/ month after your trial';
      note.textContent = 'Start today and pay nothing for ' + state.trial_days + ' days. Cancel anytime before your trial ends.';
      btn.textContent = 'Start free trial';
    } else {
      badge.textContent = 'No active plan';
      note.textContent = 'Subscribe to keep using the assistant. You can cancel anytime.';
      btn.textContent = 'Subscribe';
    }
    badge.classList.toggle('active', active);
    note.classList.toggle('active', active);
  }

  btn.addEventListener('click', function () {
    var label = btn.textContent;
    errBox.hidden = true; btn.disabled = true; btn.textContent = 'Opening secure checkout…';
    function reset() { btn.disabled = false; btn.textContent = label; }
    api('POST', '/api/billing/subscribe').then(function (r) {
      if (typeof Razorpay === 'undefined') { reset(); showError("The checkout couldn't load. Check your connection or ad blocker and try again."); return; }
      var checkout = new Razorpay({
        key: r.key_id, subscription_id: r.subscription_id, name: r.name, description: r.description, prefill: r.prefill,
        theme: { color: '#bd5b3a' },
        modal: { ondismiss: reset },
        handler: function (resp) {
          btn.textContent = 'Confirming…';
          api('POST', '/api/billing/verify', resp).then(function (v) { billing = v.billing; render(); VMA.toast('You are all set.'); },
            function (e) { reset(); showError(e.message); });
        }
      });
      checkout.on('payment.failed', function (f) { reset(); showError((f.error && f.error.description) || 'The payment did not go through.'); });
      checkout.open();
    }, function (e) { reset(); showError(e.message); });
  });

  cancelBtn.addEventListener('click', function () {
    if (!confirm('Cancel your plan? You keep access until the end of the period already covered.')) return;
    cancelBtn.disabled = true;
    api('POST', '/api/billing/cancel').then(function (r) { billing = r.billing; cancelBtn.disabled = false; render(); VMA.toast('Plan cancelled.'); },
      function (e) { cancelBtn.disabled = false; showError(e.message); });
  });

  render();
})();
