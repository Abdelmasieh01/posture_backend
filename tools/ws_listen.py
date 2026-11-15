import asyncio
import websockets


async def main():
    async with websockets.connect("ws://localhost:8000/ws/notifications/") as ws:
        print("Connected, waiting for messages...")
        async for msg in ws:
            print(msg)


asyncio.run(main())