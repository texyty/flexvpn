import asyncio
import os
import signal
import sys

from aiohttp import web


async def health(_request):
    return web.Response(text="ok")


async def run() -> None:
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    bot = await asyncio.create_subprocess_exec(sys.executable, "main.py")
    try:
        await bot.wait()
    finally:
        if bot.returncode is None:
            bot.send_signal(signal.SIGTERM)
            await bot.wait()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run())
