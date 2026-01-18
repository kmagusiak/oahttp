from __future__ import annotations

import asyncio
import logging
import os
import socket

from .router import HttpStrategy

_logger = logging.getLogger(__name__)


def listen(host='0.0.0.0', port=8080, reuse=False) -> socket.socket:
    # Unix socket
    if host.startswith('/'):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        path = host
        if reuse:
            try:
                os.unlink(path)
            except OSError:
                pass
        sock.bind(path)
        os.chmod(path, 0o660)
        sock.listen()
        _logger.info("Listening on unix:%s", path)
        return sock

    # systemd socket activation
    if host == 'systemd':
        if not (os.getenv('LISTEN_FDS') >= '1'):
            raise RuntimeError('LISTEN_FDS not set')
        if int(os.getenv('LISTEN_PID')) != os.getpid():
            raise RuntimeError('PARENT_ID does not match')
        sock = socket.socket(fileno=3)
        _logger.info("Listening on systemd activated socket")
        return sock

    # TCP socket
    # similar to socket.create_server
    if host == '::' and socket.has_dualstack_ipv6():
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        socket.create_server()
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if reuse:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR | socket.SO_REUSEPORT, 1)
    sock.bind((host, port))
    sock.listen()
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
