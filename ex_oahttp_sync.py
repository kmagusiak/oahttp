import asyncio
import logging

import uvloop

from oahttp.demo import strategy
from oahttp.http_connection import SyncTransport
from oahttp.server import listen

logging.basicConfig(level=logging.DEBUG)


def main():
    asyncio.set_event_loop(uvloop.new_event_loop())
    with listen(port=15555, reuse=True) as sock:
        while True:
            cli, _addr = sock.accept()
            transport = SyncTransport(cli)
            protocol = strategy.new_connection()
            transport.set_protocol(protocol)
            transport.run()


if __name__ == '__main__':
    main()
