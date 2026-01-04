import asyncio
import logging

import uvloop

from oahttp.demo import strategy

logging.basicConfig(level=logging.INFO)


async def main():
    loop = asyncio.get_running_loop()
    server = await loop.create_server(
        strategy.new_connection,
        '0.0.0.0',
        15555,
        reuse_address=True,
        reuse_port=True,
    )

    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    uvloop.run(main())
