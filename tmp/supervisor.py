from __future__ import annotations

import asyncio
import logging
import socket
from oahttp.http_connection import Request, HttpConnection
from oahttp.server import listen

_logger = logging.getLogger(__name__)


class HttpServer:
    def __init__(self, socket: socket.socket, router, loop: asyncio.AbstractEventLoop | None = None):
        self.loop = loop or asyncio.get_event_loop()
        self.__server = self.loop.create_server(self, sock=socket, start_serving=False)
        self.server = None
        self.router = router
        self.max_connections = asyncio.Semaphore(10)

    def __call__(self) -> HttpConnection:
        return HttpConnection(self)
    
    def _callback(self, connection):
        if connection.state == 'pre':
            server = self.server
            assert server is not None
            self.max_connections -= 1
            if self.max_connections <= 0:
                server.close()
        elif connection.state == 'closed':
            server = self.__server.server
            assert server is not None
            if self.max_connections == 0:
                self.loop.create_task(server.start_serving())
            self.max_connections += 1
    
    def run(self):
        self.loop.run_until_complete(self.arun())
    
    async def arun(self):
        self.server = await self.__server
        await self.server.serve_forever()


class WebApplication:
    backgrounds: list[BackgroundThread]
    webs: list[WebThread]
    async_code: AsyncWorker

    def __init__(self):
        self.server = HttpServer(listen(), router=self)
        self.webs = [WebThread()]
        self.async_code = AsyncWorker()

    def dispatch(self, req: Request):
        # router API
        ...

    def run(self):
        self.server.run()

    ...

class WorkerThread:
    pass

class WebThread(WorkerThread):
    ...

class BackgroundThread(WorkerThread):
    ...

class AsyncWorker(WorkerThread):
    ...


# WITH SUPERVISOR

class Supervisor:
    app: WebApplicationProcess  # one? in a process?
    webs: list[WebWorker]
    backgrunds: list[BackgroundWorker]

class WorkerProcess:
    pass

class WebApplicationProcess(WorkerProcess):
    app: WebApplication

class WebWorker(WorkerProcess):
    threads: list[WebThread]

class BackgroundWorker(WorkerProcess):
    threads: list[BackgroundThread]
