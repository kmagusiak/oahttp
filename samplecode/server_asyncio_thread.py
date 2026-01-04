import asyncio
import socket
import hashlib
import queue
import threading
import time

def work(data):
    h = hashlib.sha1(data)
    time.sleep(0.001)
    return (h.hexdigest() + '\n').encode()

qin = queue.Queue()


def worker(loop):
    while True:
        job, data = qin.get()
        out = work(data)
        loop.call_soon_threadsafe(lambda j=job, o=out: j.set_result(o))

async def nowait(f, *args):
    while True:
        try:
            return f(*args)
        except Exception:
            await asyncio.sleep(0.001)

async def handle_client(client):
    loop = asyncio.get_event_loop()
    buf = memoryview(bytearray(500))
    sending = None
    while True:
        c = await loop.sock_recv_into(client, buf)
        job = asyncio.Future()
        if c:
            await nowait(qin.put_nowait, (job, buf[:c]))
        if sending:
            await sending
        if not c:
            break
        response = await job
        sending = loop.sock_sendall(client, response)
    client.close()

async def run_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('localhost', 15555))
    server.listen(100)
    server.setblocking(False)

    loop = asyncio.get_event_loop()
    thread = threading.Thread(target=worker, args=(loop,), daemon=True)
    thread.start()

    while True:
        client, _ = await loop.sock_accept(server)
        loop.create_task(handle_client(client))

asyncio.run(run_server())
