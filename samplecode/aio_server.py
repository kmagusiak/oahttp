import uvloop

from aiohttp import web

loop = uvloop.new_event_loop()


async def proxy_handler(request: web.Request):
    return web.Response(body="hello world")

app = web.Application()
app.router.add_route("*", "/{path_info:.*}", proxy_handler)

if __name__ == "__main__":
    web.run_app(app, port=15555, loop=loop)
