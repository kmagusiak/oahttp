import asyncio
import socket
import hashlib
from concurrent.futures import ThreadPoolExecutor
import time

def work(data):
    h = hashlib.sha1(data)
    time.sleep(0.001)
    return (h.hexdigest() + '\n').encode()


async def handle_client(pool, client):
    loop = asyncio.get_event_loop()
    buf = memoryview(bytearray(500))
    sending = None
    while True:
        c = await loop.sock_recv_into(client, buf)
        #job = asyncio.to_thread(work, buf[:c])
        job = loop.run_in_executor(pool, work, buf[:c])
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

    with ThreadPoolExecutor(max_workers=1) as pool:
        while True:
            client, _ = await loop.sock_accept(server)
            loop.create_task(handle_client(pool, client))

asyncio.run(run_server())
