# RecoverOS Live Mode Guide & Verification

RecoverOS supports two execution environments:
1. **Deterministic Mock Simulation (Default):** Runs with `AI_PROVIDER=mock` and `RAZORPAY_LIVE_EXECUTION=false`. Fully offline, zero network dependencies, 100% reproducible for test suites, benchmarks, and demo walk-throughs.
2. **Live Test-Mode API Execution:** Runs against Google Gemini 1.5 Flash API for semantic intelligence and the real Razorpay Test Mode REST API (`api.razorpay.com/v1`) for live hosted payment links and cancellations.

---

## 1. Environment Variables for Live Mode

To enable live mode, set the following environment variables:

| Variable | Default | Live Mode Setting | Description |
|---|---|---|---|
| `AI_PROVIDER` | `mock` | `gemini` | Switches diagnosis engine from deterministic mock to Gemini 1.5 Flash |
| `GEMINI_API_KEY` | `""` | `AIzaSy...` | Your Google AI Studio API key |
| `RAZORPAY_LIVE_EXECUTION` | `false` | `true` | Enables real HTTP requests to Razorpay REST API |
| `RAZORPAY_KEY_ID` | `rzp_test_placeholder` | `rzp_test_...` | Genuine Razorpay Test Mode Key ID |
| `RAZORPAY_KEY_SECRET` | `changeme_key_secret` | `...` | Genuine Razorpay Test Mode Key Secret |
| `RAZORPAY_WEBHOOK_SECRET` | `changeme_webhook_secret` | `...` | Webhook signing secret configured in Razorpay Dashboard |

---

## 2. One-Step Verification: `verify_live_mode.py`

Run the included verification script:

### Linux / macOS:
```bash
export GEMINI_API_KEY="your-gemini-api-key"
export AI_PROVIDER="gemini"
export RAZORPAY_KEY_ID="rzp_test_yourkey"
export RAZORPAY_KEY_SECRET="yourkeysecret"
export RAZORPAY_LIVE_EXECUTION="true"

python backend/scripts/verify_live_mode.py
```

### Windows (PowerShell):
```powershell
$env:GEMINI_API_KEY="your-gemini-api-key"
$env:AI_PROVIDER="gemini"
$env:RAZORPAY_KEY_ID="rzp_test_yourkey"
$env:RAZORPAY_KEY_SECRET="yourkeysecret"
$env:RAZORPAY_LIVE_EXECUTION="true"

python backend/scripts/verify_live_mode.py
```

---

## 3. What the Verification Script Executes

1. **Gemini 1.5 Flash:**
   - Sends a structured `DiagnosisRequest` for an `expired_card` failure to `https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent`.
   - Uses strict JSON schema generation config.
   - Validates that the returned response adheres to `DiagnosisResponse` Pydantic model with category, confidence, and action probabilities.

2. **Razorpay Test Mode REST API:**
   - Sends `POST https://api.razorpay.com/v1/payment_links` authenticated with Basic Auth (`key_id:key_secret`).
   - Generates a genuine hosted short URL (`https://rzp.io/i/{id}`).
   - Immediately cancels the generated test link via `POST https://api.razorpay.com/v1/payment_links/{id}/cancel` to ensure clean sandbox hygiene.

---

## 4. Running the Full Application in Live Mode

To run the complete control plane and golden demo with live API execution:

```bash
# 1. Export live credentials
export GEMINI_API_KEY="your-key"
export AI_PROVIDER="gemini"
export RAZORPAY_KEY_ID="rzp_test_yourkey"
export RAZORPAY_KEY_SECRET="yourkeysecret"
export RAZORPAY_LIVE_EXECUTION="true"

# 2. Start the FastAPI control plane
uvicorn backend.app.main:app --reload

# 3. Run the Golden Demo against live APIs
python backend/scripts/golden_demo.py
```
