from __future__ import annotations

import asyncio
import logging
import socket

from .router import HttpStrategy

_logger = logging.getLogger(__name__)


def listen(host='0.0.0.0', port=8080, reuse=False) -> socket.socket:
    # TODO https://docs.python.org/3/library/socket.html
    if socket.has_dualstack_ipv6() and False:
        family = socket.AF_INET6
    else:
        family = socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if reuse:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR | socket.SO_REUSEPORT, 1)
    sock.bind((host, port))
    sock.listen()  # set a limit?
    _logger.info("Listening on %s:%s", host, port)
    return sock


async def accept_forever(
    sock: socket.socket, strategy: HttpStrategy, can_accept: asyncio.Event | None = None
):
    loop = asyncio.get_running_loop()
    while True:
        if can_accept is not None:
            await can_accept.wait()
        cli, addr = await loop.sock_accept(sock)
        _logger.debug("%s: accepted connection from %s", strategy, addr)
        await loop.connect_accepted_socket(strategy.new_connection, cli)
        del cli, addr
