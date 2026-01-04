import asyncio
import logging

import uvloop

from oahttp.demo import strategy
from oahttp.server import accept_forever, listen

logging.basicConfig(level=logging.DEBUG)


async def main():
    with listen(port=15555, reuse=True) as sock:
        try:
            await accept_forever(sock, strategy)
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    uvloop.run(main())
