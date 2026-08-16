import asyncio
from workers import send_dms
import sys

async def main():
    try:
        await asyncio.wait_for(send_dms(), timeout=5.0)
    except Exception as e:
        print(f"Failed: {e}")

asyncio.run(main())
