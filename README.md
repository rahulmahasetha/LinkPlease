# LinkPlease Tech Intern Assignment

**Author:** Rahul Mahaseth  
**Live Deployment:** [https://linkplease-uxsh.onrender.com](https://linkplease-uxsh.onrender.com)  
**Loom Walkthrough:** [Insert Loom URL Here]  
**Parts Completed:** A + B + C  

---

## 📖 Project Overview

LinkPlease is a resilient, automated DM system for creators. When a follower comments a specific keyword (e.g., `PRICE`) on a creator's post, this backend automatically triggers a direct message to that follower containing a predefined response. 

This project was built to gracefully handle the hostile realities of interacting with a simulated Instagram Mock API, specifically tackling:
* Strict 10 requests per 60 seconds rate limits
* Sudden 429 Too Many Requests and 500 Internal Server Errors
* Out-of-order, duplicated, and late-deleted comment webhooks
* Undocumented HMAC signature trap behaviors 

## ✨ Features Implemented

* **Asynchronous Webhook Processing:** Webhooks instantly return `200 OK` (well under the 5-second requirement) by offloading events to an in-memory `asyncio.Queue` for background processing.
* **Strict HMAC-SHA256 Security:** Enforces signature verification on all incoming payloads. Specifically handles the undocumented edge-case where the Mock API signs payloads using the user's base64-encoded *email address* rather than the full API key.
* **Event Deduplication (Idempotency):** Prevents spamming users by silently discarding duplicate `event_id` payloads and tracking active rule-to-user mappings.
* **Rate-Limit Respecting Queues:** Background workers smoothly throttle outbound DM requests to strictly adhere to the 10 req/60s limit using rolling timestamps.
* **Resilient Retry Logic:** Automatically queues and retries DMs when the mock API returns a `500 Internal Error` or `429 Too Many Requests` (respecting the `Retry-After` header).
* **Asynchronous Polling & Cancellation:** Continuously polls the `/v1/dm/{dm_id}` endpoint to verify actual delivery status and correctly handles late `comment.deleted` webhooks by preventing pending DMs from being sent.

---

## 🏗 Architecture & Workflow

The backend operates on a single `FastAPI` instance running three persistent `asyncio` background workers:
1. **`process_events`**: Pulls from the webhook memory queue, verifies keywords against the SQLite `rules` table, drops duplicates, and inserts valid requests into the `dms` queue.
2. **`send_dms`**: Polls the SQLite `dms` table for unsent messages, manages rolling 60-second rate limits, and safely `POST`s to the external Mock API. Handles 4xx/5xx errors gracefully.
3. **`poll_dms`**: Periodically checks the Mock API to confirm if "accepted" DMs eventually transition to `delivered` or `failed`, updating the local database accordingly.

### File Structure
```text
LinkPlease/
├── main.py              # FastAPI endpoints (/rules, /webhook, /stats) and lifecycle management
├── workers.py           # Background asyncio workers for queuing, sending, and polling DMs
├── database.py          # SQLite schema definitions and async database connection handling
├── test_webhook.py      # Local testing script to generate valid HMAC-SHA256 signatures
├── requirements.txt     # Python dependencies
├── .env.example         # Template for required environment variables
├── .gitignore           # Git ignore configurations (ignoring local .db and .env files)
└── README.md            # Project documentation
```

### Tech Stack
* **Framework:** Python 3, FastAPI, Uvicorn
* **Database:** SQLite (via `aiosqlite` for non-blocking async queries)
* **HTTP Client:** `httpx` (for asynchronous outbound requests)

---

## 📡 API Documentation

### 1. Create a Rule
Instructs the server to watch for a specific keyword in comments.
* **`POST /rules`**
* **Body:**
```json
{ 
  "keyword": "PRICE", 
  "dm_message": "Here is the price list: https://example.com" 
}
```
* **Response (201 Created):**
```json
{
  "rule_id": "c978f67c-b463...",
  "keyword": "PRICE",
  "dm_message": "Here is the price list: https://example.com"
}
```

### 2. Receive Webhooks
Receives incoming comment events from the Mock API. Must include valid HMAC signature.
* **`POST /webhook`**
* **Headers:** `X-PseudoGram-Signature: sha256=<hash>`
* **Response:** `200 OK` (Instantly)

### 3. Analytics
Retrieves live processing statistics.
* **`GET /stats`**
* **Response:**
```json
{
  "sent": 142,
  "failed": 3,
  "queued": 8,
  "duplicates_blocked": 57
}
```

---

## 🛠 Local Setup & Testing

### 1. Environment Variables
Create a `.env` file in the root directory:
```env
API_KEY=your_base64_encoded_api_key_here
API_BASE_URL=https://pseudogram-api.onrender.com
```

### 2. Run Locally
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### 3. Test the Load Simulator
To run a 500-event stress test against the live deployment:
1. Create a rule using `POST /rules`.
2. Send the following request via Postman to the Mock API:
* **`POST https://pseudogram-api.onrender.com/v1/simulate/start`**
* **Headers:** `X-API-Key: <your_api_key>`
* **Body:**
```json
{ 
    "webhook_url": "https://linkplease-uxsh.onrender.com/webhook", 
    "count": 500, 
    "duration_seconds": 10 
}
```
3. Wait 60 seconds and hit **`GET /stats`** on the local or Render deployment to watch the queue drain and the `sent` count rise.

---

## ☁️ Render Deployment

1. Push this repository to GitHub.
2. Create a new **Web Service** on Render connected to the repository.
3. Configure settings:
   * **Environment:** `Python 3`
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add `API_KEY` and `API_BASE_URL` as Environment Variables in the Render dashboard.
5. Deploy.

---

## ⚠️ Known Failures & Edge Cases
As required by the assignment, architectural limitations and edge cases have been explicitly documented in `FAILURES.md` at the root of this repository. This includes issues like Head-of-Line blocking on 500 errors and webhook event loss during server restarts.
