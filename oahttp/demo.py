import asyncio
import json
from .response import GenericResponse
from .router import HttpStrategy, Router
from concurrent.futures import ThreadPoolExecutor

strategy = HttpStrategy()
global_pool = ThreadPoolExecutor()
strategy.dispatcher = Router()
route = strategy.dispatcher.route


@route('/sleep').get()
async def _sleep(request):
    await asyncio.sleep(1)
    return GenericResponse(request.target + b'\n', status=b'200 OK')

@route('/exec').get()
async def _exec():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(global_pool, lambda: GenericResponse(b'ok\n', status=b'200 OK'))

@route('/...')
async def _default(request):
    response = b'ok!\n'
    if b'/echo' in request.target:
        data = {
            ':method': request.method,
            ':path': request.target.decode(),
            ':version': request.http_version.decode(),
        }
        data.update({
            k.decode(): v.decode()
            for k, v in request.headers.items()
        })
        response = json.dumps(data).encode()
    return GenericResponse(response, status=b'200 OK')
