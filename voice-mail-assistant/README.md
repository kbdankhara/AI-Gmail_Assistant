# Voice Mail Assistant

A web app that reads your Gmail aloud, summarizes each message with AI and sends the replies you
dictate and approve. Built from the "Assistant Walkthrough" design.

## Try it in two minutes (no credentials)

```bash
pip install -r requirements.txt
DEMO_MODE=1 DEV_LOGIN=1 flask --app wsgi run
```

Open http://localhost:5000 in Chrome or Edge. Demo mode signs you in without Google, uses a sample
inbox of three emails and canned summaries, and sends nothing. Type any 16 letters as the App
Password. Voice is real: allow the microphone and talk to it.

## Going live

1. `cp .env.example .env`, then `python manage.py generate-key` and paste both keys in.
   Keep `ENCRYPTION_KEY` safe: without it, saved App Passwords and API keys can't be read.
2. **Google sign-in.** In Google Cloud Console create an OAuth client (Web application) with the
   redirect URI `<BASE_URL>/auth/google/callback`. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
3. **Razorpay.** Set `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`, run `python manage.py create-plan`
   and set the printed `RAZORPAY_PLAN_ID`. Add a webhook pointing at `<BASE_URL>/webhooks/razorpay`
   with the `subscription.*` events and set `RAZORPAY_WEBHOOK_SECRET` to its secret.
   The plan is created in `PLAN_CURRENCY` (USD by default); charging in a currency other than INR
   needs international payments enabled on the Razorpay account.
4. Serve over **https** (the microphone needs it) and set `BASE_URL` to the public address.
5. Run with `gunicorn wsgi:app` or the included `Dockerfile`. Leave `DEMO_MODE` and `DEV_LOGIN` off.

Until the Razorpay settings are filled in, billing is off and the assistant is free to use.
Once they are, the assistant only turns on during a trial or paid plan.

## How it works

| Part | Where | Notes |
| --- | --- | --- |
| Sign-in, consent | `app/auth.py`, `app/api.py` | Google OpenID; four consents recorded with policy version and time |
| Gmail | `app/gmail.py` | IMAP and SMTP with an App Password per inbox; emails are fetched on demand and never stored |
| AI | `app/ai.py` | Claude, Gemini, OpenAI or Groq with the user's own key; falls back to plain text without one |
| Billing | `app/billing.py` | Razorpay subscription whose first charge is `TRIAL_DAYS` away; checkout and webhook signatures verified |
| Voice loop | `app/static/assistant.js` | Browser speech recognition and synthesis; greet, read, reply, mark read, skip, watch every 30 s |
| Screens | `app/templates/`, `app/static/style.css` | Sign in, consent, profile, assistant, payment, plus landing, help, privacy, terms, errors |

Secrets are encrypted with Fernet before they are stored. A reply is sent only when the request
carries the user's approval, after the draft has been read back.

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                   # API, security and billing tests
DEMO_MODE=1 DEV_LOGIN=1 flask --app wsgi run --port 5055 &
python tests/e2e_walkthrough.py          # browser run through every screen
```

## Before launch

- The Privacy Policy and Terms are a plain-language draft that matches what the app does. Have
  them reviewed, and set `SUPPORT_EMAIL`.
- SQLite is fine for one server. Set `DATABASE_URL` to Postgres before running several workers.
