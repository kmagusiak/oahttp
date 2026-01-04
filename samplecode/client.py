import socket
import sys
from concurrent.futures import ThreadPoolExecutor

with open('/tmp/req', 'rb') as f:
    data = f.read()

def run():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(('localhost', 15555))
        s.sendall(data)
        #s.shutdown(socket.SHUT_WR)
        v = 0
        while c := s.recv(1000):
            v += len(c)
    return v

cnt = int(sys.argv[1])
with ThreadPoolExecutor(4) as exec:
    fs = list(exec.map(lambda _: run(), range(cnt)))
print(len(fs), min(fs), max(fs))
