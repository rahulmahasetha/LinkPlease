import hmac
import hashlib
import os
import uuid
import asyncio
from fastapi import FastAPI, Request, HTTPException, Header, BackgroundTasks
from pydantic import BaseModel
import sqlite3

from database import init_db, get_db
from workers import process_events, send_dms, poll_dms, event_queue, API_KEY

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "LinkPlease backend is running!"}

class RuleCreate(BaseModel):
    keyword: str
    dm_message: str

background_tasks = set()

@app.on_event("startup")
async def startup_event():
    await init_db()
    # Start background workers
    task1 = asyncio.create_task(process_events())
    task2 = asyncio.create_task(send_dms())
    task3 = asyncio.create_task(poll_dms())
    background_tasks.update({task1, task2, task3})

@app.post("/rules", status_code=201)
async def create_rule(rule: RuleCreate):
    rule_id = str(uuid.uuid4())
    db = await get_db()
    await db.execute(
        "INSERT INTO rules (rule_id, keyword, dm_message) VALUES (?, ?, ?)",
        (rule_id, rule.keyword, rule.dm_message)
    )
    await db.commit()
    await db.close()
    return {"rule_id": rule_id, "keyword": rule.keyword, "dm_message": rule.dm_message}

@app.post("/webhook", status_code=200)
async def webhook(request: Request, x_pseudogram_signature: str = Header(None)):
    if not x_pseudogram_signature:
        raise HTTPException(status_code=401, detail="Missing signature")
    
    body = await request.body()
    
    # Verify signature
    # Header format: sha256=<hex>
    if not x_pseudogram_signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid signature format")
        
    signature = x_pseudogram_signature.split("=")[1]
    # The mock API actually signs webhooks using the email address, not the full API key!
    # The first part of the API key is the base64-encoded email.
    import base64
    try:
        b64_email = API_KEY.split('.')[0]
        # Add necessary padding for base64 decoding
        padding_needed = len(b64_email) % 4
        if padding_needed:
            b64_email += '=' * (4 - padding_needed)
        secret = base64.b64decode(b64_email).decode('utf-8')
    except:
        secret = API_KEY
        
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        body,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event_id")
        
    db = await get_db()
    
    # Deduplicate based on event_id
    try:
        await db.execute("INSERT INTO events (event_id) VALUES (?)", (event_id,))
        await db.commit()
    except sqlite3.IntegrityError:
        # Event already processed (redelivery)
        await db.close()
        return {"status": "ok"}
    
    if event_type == "comment.deleted":
        comment_id = payload.get("data", {}).get("comment_id")
        if comment_id:
            try:
                await db.execute("INSERT INTO deleted_comments (comment_id) VALUES (?)", (comment_id,))
                await db.commit()
            except sqlite3.IntegrityError:
                pass
    elif event_type == "comment.created":
        # Put into asyncio queue for background processing
        await event_queue.put(payload)

    await db.close()
    return {"status": "ok"}

@app.get("/stats")
async def get_stats():
    db = await get_db()
    stats = {
        "sent": 0,
        "failed": 0,
        "queued": 0,
        "duplicates_blocked": 0
    }
    
    # Get duplicates_blocked from stats table
    async with db.execute("SELECT value FROM stats WHERE key = 'duplicates_blocked'") as cursor:
        row = await cursor.fetchone()
        if row:
            stats["duplicates_blocked"] = row["value"]
            
    # Get other counts from dms table
    async with db.execute("SELECT status, COUNT(*) as count FROM dms GROUP BY status") as cursor:
        rows = await cursor.fetchall()
        for row in rows:
            status = row["status"]
            count = row["count"]
            if status == "sent":
                stats["sent"] = count
            elif status == "failed":
                stats["failed"] = count
            elif status == "queued":
                stats["queued"] = count

    await db.close()
    return stats
