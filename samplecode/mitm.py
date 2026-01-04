import socket
import selectors
from concurrent.futures import ThreadPoolExecutor
import logging

log = logging.getLogger(__name__)
pool = ThreadPoolExecutor(thread_name_prefix="con")

def listen(port):
    family = socket.AF_INET
    log.debug("Opening the socket")
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.bind(('0.0.0.0', port))
    sock.listen()
    log.info("Listening... %s", sock)
    return sock


def main(listen_port, to_port):
    def handle(src):
        dest = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        dest.connect(('localhost', to_port))
        sel = selectors.DefaultSelector()
        sel.register(src, selectors.EVENT_READ)
        sel.register(dest, selectors.EVENT_READ)
        with dest, sel:
            while True:
                try:
                    items = sel.select()
                except ValueError:
                    break
                for key, ev in items:
                    sock_from = key.fileobj
                    sock_to = src if sock_from is dest else dest
                    data = sock_from.read()
                    sock_to.writeall(data)

    with listen(listen_port) as server:
        cli, addr = server.accept()
        log.debug("accepted %s", cli)
        pool.submit(lambda: handle(cli))




if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main(8000, 15555)
