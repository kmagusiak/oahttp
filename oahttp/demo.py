import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from .request import Request
from .response import Ok
from .router import HttpStrategy, Router

strategy = HttpStrategy()
global_pool = ThreadPoolExecutor()
strategy.dispatcher = Router()
route = strategy.dispatcher.route


@route('/sleep').get()
async def _sleep(request: Request):
    await asyncio.sleep(1)
    return Ok(request.target.encode() + b'\n')


@route('/exec').get()
async def _exec():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(global_pool, lambda: Ok(b'ok\n'))


@route('/...')
async def _default(request: Request):
    response = b'ok!\n'
    if '/echo' in request.target:
        data = {
            ':method': request.method,
            ':path': request.target,
            ':version': request.http_version.decode(),
        }
        data.update({k.decode(): v.decode() for k, v in request.headers.items()})
        response = json.dumps(data).encode()
    return Ok(response)
