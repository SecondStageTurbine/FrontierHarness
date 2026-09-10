"""Read-only discovery. Prints model IDs only, never credentials or provider payloads."""
import asyncio
import os
from anthropic import AsyncAnthropic

async def main():
    try:
        async with AsyncAnthropic(api_key=os.environ['ANTHROPIC_API_KEY'],max_retries=0) as client:
            page = await client.models.list(limit=30)
            print('\n'.join(m.id for m in page.data))
    except Exception as exc:
        print('Provider discovery failed:',type(exc).__name__,getattr(exc,'status_code',None))

if __name__=='__main__':
    asyncio.run(main())
