import logging
import os

from oahttp.server import listen

log_level = logging.DEBUG
if os.getenv('PERF'):
    log_level = logging.WARNING
logging.basicConfig(level=log_level)


def main():
    with listen(port=15555, reuse=True) as sock:
        buf = bytearray(4096)
        while True:
            cli, _addr = sock.accept()
            cli.recv_into(buf)
            cli.sendall(b'HTTP/1.0 200 OK\r\ncontent-length: 3\r\n\r\nok!')
            cli.close()


if __name__ == '__main__':
    main()
