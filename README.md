# Minimal HTTP server

Do as little as possible and still be a valid HTTP 1.1 origin server used as an
application server.
Note that a web server should handle client timeouts, limits and other
protections. Use *nginx* or an alternative web server.

WSGI and ASGI is not implemented. Not aiming to be compatible with the ecosystem
because alternatives exist. Here, we try to strip as many abstractions as
possible and still run fast.

## Features

Connection:
Keep track of the connection by implementing an asyncio protocol.
This class handles connection management (keep-alive)
and protocol upgrade.

### Out of scope
This is just the origin server: not a proxy, no CONNECT.
