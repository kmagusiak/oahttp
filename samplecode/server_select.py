import functools
import logging
import selectors
import socket

from data import work

_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def listen():
    # TODO https://docs.python.org/3/library/socket.html
    if socket.has_dualstack_ipv6() and False:
        family = socket.AF_INET6
    else:
        family = socket.AF_INET
    _logger.debug("Opening the socket")
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.bind(('0.0.0.0', 15555))
    sock.setblocking(False)
    sock.listen()  # XXX set a limit
    _logger.info("Listening... %s", sock)
    return sock

def handle_all(server_sock: socket.socket):
    sel = selectors.DefaultSelector()

    def accept(sock):
        conn, addr = sock.accept()
        conn.setblocking(False)
        buf = bytearray()
        sel.register(conn, selectors.EVENT_READ, functools.partial(handle, buf))

    def handle(out_buf, s):
        data = s.recv(500)
        s.sendall(work(data))
        sel.unregister(s)
        s.close()
        return

    with sel:
        sel.register(server_sock, selectors.EVENT_READ, accept)
        while True:
            for key, _ in sel.select():
                callback = key.data
                callback(key.fileobj)

if __name__ == '__main__':
    with listen() as s:
        handle_all(s)
