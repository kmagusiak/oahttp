import logging
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
    sock.setblocking(True)
    sock.listen()  # XXX set a limit
    _logger.info("Listening... %s", sock)
    return sock

def handle_one(sock: socket.socket):
    s, addr = sock.accept()
    #_logger.info("Accepted connection: %r from %s", s, addr)
    def rest():
        buf = memoryview(bytearray(500))
        while cnt := s.recv_into(buf):
            s.sendall(work(buf[:cnt]))
            break
        s.close()
    rest()
   

if __name__ == '__main__':
    with listen() as s:
        while True:
            handle_one(s)
