import asyncio
import httpx
import os
import time
from database import get_db
import sqlite3
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("API_KEY")
API_BASE_URL = os.environ.get("API_BASE_URL", "https://pseudogram-api.onrender.com")

event_queue = asyncio.Queue()

async def process_events():
    """Processes comment.created events from the queue."""
    while True:
        event = await event_queue.get()
        try:
            db = await get_db()
            data = event.get("data", {})
            comment_id = data.get("comment_id")
            text = data.get("text", "")
            user_id = data.get("from", {}).get("user_id")
            
            # Check if comment was already deleted
            async with db.execute("SELECT 1 FROM deleted_comments WHERE comment_id = ?", (comment_id,)) as cursor:
                if await cursor.fetchone():
                    event_queue.task_done()
                    await db.close()
                    continue
            
            # Find matching rules
            async with db.execute("SELECT rule_id, keyword FROM rules") as cursor:
                rules = await cursor.fetchall()
            
            for rule in rules:
                rule_id = rule["rule_id"]
                keyword = rule["keyword"].lower()
                if keyword in text.lower():
                    # Match found. Check if user already DMed for this rule.
                    try:
                        await db.execute(
                            "INSERT INTO dms (user_id, rule_id, comment_id, status) VALUES (?, ?, ?, 'queued')",
                            (user_id, rule_id, comment_id)
                        )
                        await db.commit()
                    except sqlite3.IntegrityError:
                        # user_id + rule_id already exists. Increment duplicates_blocked
                        await db.execute("UPDATE stats SET value = value + 1 WHERE key = 'duplicates_blocked'")
                        await db.commit()
            
            await db.close()
        except Exception as e:
            print(f"Error processing event: {e}")
        finally:
            event_queue.task_done()

async def send_dms():
    """Polls queued DMs and sends them, respecting the 10 req/60s rate limit."""
    timestamps = []
    
    async with httpx.AsyncClient() as client:
        while True:
            try:
                db = await get_db()
                print("Connected to DB in send_dms")
                
                # Fetch one queued DM that hasn't been sent to external API yet
                async with db.execute(
                    "SELECT id, user_id, rule_id, comment_id FROM dms WHERE status = 'queued' AND external_dm_id IS NULL LIMIT 1"
                ) as cursor:
                    dm = await cursor.fetchone()
                
                if not dm:
                    await db.close()
                    await asyncio.sleep(0.5)
                    continue
                print(f"Found DM to send: {dm['id']}")
                
                # Check if comment was deleted since queueing
                async with db.execute("SELECT 1 FROM deleted_comments WHERE comment_id = ?", (dm["comment_id"],)) as cursor:
                    if await cursor.fetchone():
                        # Don't send, mark failed or cancelled. We'll mark failed.
                        await db.execute("UPDATE dms SET status = 'failed' WHERE id = ?", (dm["id"],))
                        await db.commit()
                        await db.close()
                        continue
                
                # We need to send it. Get the dm_message.
                async with db.execute("SELECT dm_message FROM rules WHERE rule_id = ?", (dm["rule_id"],)) as cursor:
                    rule = await cursor.fetchone()
                if not rule:
                    # Rule deleted? Skip.
                    await db.execute("UPDATE dms SET status = 'failed' WHERE id = ?", (dm["id"],))
                    await db.commit()
                    await db.close()
                    continue
                
                message = rule["dm_message"]
                
                # Wait for rate limit
                now = time.time()
                timestamps = [t for t in timestamps if now - t < 60]
                if len(timestamps) >= 10:
                    wait_time = 60 - (now - timestamps[0])
                    await db.close()
                    await asyncio.sleep(wait_time)
                    continue # Retry after sleep
                
                timestamps.append(time.time())
                
                payload = {
                    "recipient_user_id": dm["user_id"],
                    "message": message,
                    "comment_id": dm["comment_id"]
                }
                
                response = await client.post(
                    f"{API_BASE_URL}/v1/dm/send",
                    json=payload,
                    headers={"X-API-Key": API_KEY, "Idempotency-Key": f"dm_{dm['id']}"}
                )
                print(f"Sent DM {dm['id']}, status: {response.status_code}")
                
                if response.status_code in (200, 202):
                    data = response.json()
                    external_dm_id = data.get("dm_id")
                    await db.execute("UPDATE dms SET external_dm_id = ? WHERE id = ?", (external_dm_id, dm["id"]))
                    await db.commit()
                elif response.status_code == 429:
                    # Rate limited. The external API says we violated it, despite our local tracking.
                    data = response.json()
                    retry_after = int(response.headers.get("Retry-After", 6))
                    print(f"Rate limited on DM {dm['id']}, waiting {retry_after} seconds.")
                    # Don't update status, we will retry.
                    # Just sleep.
                    await asyncio.sleep(retry_after)
                elif response.status_code == 500:
                    # Internal Error. Will retry later.
                    pass
                elif response.status_code == 400:
                    # Bad Request. Cannot retry.
                    await db.execute("UPDATE dms SET status = 'failed' WHERE id = ?", (dm["id"],))
                    await db.commit()
                
                await db.close()
            except Exception as e:
                print(f"Error in send_dms: {e}")
                await asyncio.sleep(1)


async def poll_dms():
    """Polls external DM status for DMs that are queued but have an external ID."""
    async with httpx.AsyncClient() as client:
        while True:
            try:
                db = await get_db()
                async with db.execute(
                    "SELECT id, external_dm_id FROM dms WHERE status = 'queued' AND external_dm_id IS NOT NULL"
                ) as cursor:
                    dms = await cursor.fetchall()
                
                if not dms:
                    await db.close()
                    await asyncio.sleep(1)
                    continue
                
                for dm in dms:
                    external_dm_id = dm["external_dm_id"]
                    response = await client.get(
                        f"{API_BASE_URL}/v1/dm/{external_dm_id}",
                        headers={"X-API-Key": API_KEY}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        status = data.get("status")
                        if status in ("delivered", "failed"):
                            new_status = 'sent' if status == 'delivered' else 'failed'
                            await db.execute("UPDATE dms SET status = ? WHERE id = ?", (new_status, dm["id"]))
                            await db.commit()
                    # Sleep slightly to avoid overwhelming the server, even though it's not rate-limited
                    await asyncio.sleep(0.1)
                
                await db.close()
                await asyncio.sleep(2)
            except Exception as e:
                print(f"Error in poll_dms: {e}")
                await asyncio.sleep(1)
