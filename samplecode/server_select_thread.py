import logging
import functools
import socket
import hashlib
import time
import selectors
from concurrent.futures import ThreadPoolExecutor


_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def work(data):
    h = hashlib.sha1(data)
    time.sleep(0.001)
    return (h.hexdigest() + '\n').encode()


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
    pool = ThreadPoolExecutor(1)
    pool.submit(lambda: None)

    def accept(sock):
        conn, addr = sock.accept()
        conn.setblocking(False)
        buf = []
        sel.register(conn, selectors.EVENT_READ, functools.partial(handle, buf))

    def handle(out_buf, s):
        data = s.recv(500)
        if not data:
            sel.unregister(s)
            for x in out_buf:
                x.result()
            s.close()
            return
        # use out-buf to write what is possible
        sent = pool.submit(lambda: s.send(work(data)))
        out_buf.append(sent)

    sel.register(server_sock, selectors.EVENT_READ, accept)
    while True:
        for key, _ in sel.select():
            callback = key.data
            callback(key.fileobj)

with listen() as s:
    handle_all(s)
