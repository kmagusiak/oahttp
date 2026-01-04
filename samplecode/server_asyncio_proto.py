import asyncio
import uvloop
from data import work


class EchoServerProtocol(asyncio.BufferedProtocol):

    def connection_made(self, transport: asyncio.Transport):
        #peername = transport.get_extra_info('peername')
        self.transport = transport

    def get_buffer(self, sizehint):
        sz = 500
        self.buf = memoryview(bytearray(sz))
        return self.buf

    def buffer_updated(self, nbytes):
        data = bytes(self.buf[:nbytes])
        out = work(data)
        self.transport.write(out)
        self.transport.close()


async def main():
    # Get a reference to the event loop as we plan to use
    # low-level APIs.
    loop = asyncio.get_running_loop()

    server = await loop.create_server(
        EchoServerProtocol,
        '127.0.0.1', 15555)

    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    uvloop.run(main())
