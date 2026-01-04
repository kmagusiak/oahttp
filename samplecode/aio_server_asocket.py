import asyncio
import logging
import socket

import uvloop
from aiohttp import web

_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

def listen():
    # TODO https://docs.python.org/3/library/socket.html
    if socket.has_dualstack_ipv6() and False:
        family = socket.AF_INET6
    else:
        family = socket.AF_INET
    _logger.debug("Opening the socket")
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR | socket.SO_REUSEPORT, 1)
    sock.bind(('0.0.0.0', 15555))
    sock.listen(100)  # XXX set a limit
    _logger.info("Listening... %s", sock)
    return sock

async def proxy_handler(request: web.Request):
    return web.Response(body="hello world")

app = web.Application()
app.router.add_route("*", "/{path_info:.*}", proxy_handler)

async def main():
    loop = asyncio.get_event_loop()
    app._set_loop(loop)
    app.on_startup.freeze()
    await app.startup()
    app.freeze()

    server = app._make_handler(loop=loop)
    
    with listen() as sock:
        while True:
            cli, addr = await loop.sock_accept(sock)
            await loop.connect_accepted_socket(server, cli)
    await app.cleanup()


if __name__ == "__main__":
    uvloop.run(main())
