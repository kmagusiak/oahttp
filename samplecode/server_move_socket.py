import logging
import os
import socket

from data import work

_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# -------------------------------
# Helpers to send and receive FDs
# -------------------------------

def send_fd(sock: socket.socket, fd, data: bytes):
    """Send a single file descriptor with extra data."""
    socket.send_fds(sock, [data], [fd])

def recv_fd(sock, max_data=1024):
    data, fds, flags, addr = socket.recv_fds(sock, max_data, 1)
    return fds[0], data


# -------------------------------
# Child worker
# -------------------------------

def worker_process(uds_child):
    print(f"[CHILD] Worker started (pid={os.getpid()})")

    while True:
        fd, head = recv_fd(uds_child)
        if not fd:
            continue

        client_sock = socket.socket(fileno=fd)

        # Handle the client
        data = client_sock.recv(10000)
        client_sock.sendall(work(data))
        #client_sock.shutdown(socket.SHUT_RDWR)
        client_sock.close()


# -------------------------------
# Parent accept loop
# -------------------------------

def parent_process(uds_parent):

    # Create TCP listening socket
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR | socket.SO_REUSEPORT, 1)
    tcp_sock.bind(("127.0.0.1", 15555))
    tcp_sock.listen()

    try:
        while True:
            conn, addr = tcp_sock.accept()

            head = conn.recv(10)
            send_fd(uds_parent, conn.fileno(), head)
            conn.close()   # Child now has the FD
    finally:
        tcp_sock.close()


# -------------------------------
# Main
# -------------------------------

def main():
    # Create Unix-domain socket pair for FD passing
    uds_parent, uds_child = socket.socketpair(socket.AF_UNIX)

    # Fork once
    pid = os.fork()

    if pid == 0:
        # Child process
        uds_parent.close()
        worker_process(uds_child)
    else:
        # Parent process
        uds_child.close()
        parent_process(uds_parent)


if __name__ == "__main__":
    main()
