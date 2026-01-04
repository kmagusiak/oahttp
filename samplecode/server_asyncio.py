import socket
import asyncio
import uvloop
from data import work


async def handle_client(client):
    loop = asyncio.get_event_loop()
    buf = memoryview(bytearray(500))
    sending = None
    while True:
        c = await loop.sock_recv_into(client, buf)
        if not c:
            break
        response = work(buf[:c])
        sending = loop.sock_sendall(client, response)
        await sending
        break
    client.close()

async def run_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('localhost', 15555))
    server.listen(100)
    server.setblocking(False)

    loop = asyncio.get_event_loop()

    while True:
        client, _ = await loop.sock_accept(server)
        loop.create_task(handle_client(client))

if __name__ == '__main__':
    uvloop.run(run_server())
