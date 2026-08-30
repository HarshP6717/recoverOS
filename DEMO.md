# RecoverOS Demo Guide

This guide walks you through executing the RecoverOS Golden Demo, which proves the end-to-end functionality of the system from failure ingestion to economic decision making and closed-loop reconciliation.

## Prerequisites

1. Ensure the Python backend dependencies are installed (`pip install -r requirements.txt`).
2. Ensure the frontend Node dependencies are installed (`cd frontend && npm install`).
3. Run `python backend/scripts/reset_demo_data.py` before every live demo/judge walkthrough to start from a clean, fully-audited dataset.

## Step 1: Start the Backend Control Plane

Open a terminal and start the FastAPI server:

```bash
uvicorn backend.app.main:app --reload
```

## Step 2: Start the Merchant Command Center (UI)

Open a second terminal and start the React frontend:

```bash
cd frontend
npm run dev
```

Navigate to `http://localhost:5173` in your browser.

## Step 3: Execute the Golden Demo

Open a third terminal and run the deterministic demo script. This script acts as Razorpay, firing webhooks into the local control plane.

```bash
python backend/scripts/golden_demo.py
```

### What you will see in the terminal:

1. **Failure Simulation:** The script simulates a `payment.failed` webhook due to an `expired_card`.
2. **AI Diagnosis:** The AI correctly identifies the category and outputs probabilities.
3. **Economic Decision:** The script outputs the ERV math. You will see `recovery_link` beat `send_reminder` due to the calculation of direct costs and friction costs.
4. **Counterfactuals:** The script calculates the economic advantage generated.
5. **Execution:** The system generates a Razorpay Test Mode Payment Link.
6. **Settlement:** The script immediately fires a `payment_link.paid` webhook, simulating the customer paying the link.
7. **Reconciliation:** The system reconciles the payment and marks the journey `RECOVERED`.

## Step 4: Verify in the UI

1. Go back to `http://localhost:5173` (Merchant Command Center).
2. You will see the total KPIs updated.
3. Click on **Recovery Journeys** in the sidebar.
4. Click on the most recent journey.
5. **Observe the Investigation Page:**
   - Verify the AI Diagnosis panel.
   - Verify the Economic Decision table (showing the ERV math).
   - Observe the Counterfactual Advantage prominently displayed.
   - Look at the Execution panel to see the active Razorpay Test Link.
   - Scroll down the Audit Timeline to see the exact progression of LIVE and SIMULATED events.
