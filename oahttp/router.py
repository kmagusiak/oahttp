from __future__ import annotations

import functools
import typing
from collections.abc import Callable, Coroutine

from . import config
from .http_connection import HttpConnection, Request
from .response import (
    ClientErrorResponse,
    FileBody,
    MethodNotAllowed,
    NotAcceptable,
    NotFound,
    Ok,
    Response,
    ServerErrorResponse,
)
from .session import Session

Dispatcher = Callable[[Request], Coroutine[typing.Any, typing.Any, Response]]


async def default_dispatcher(request: Request) -> Response:
    if request.method == 'OPTIONS' and request.target == '*':
        response = Ok(None)
        response.headers[b'allow'] = b'GET, HEAD, OPTIONS'
        return response

    if request.method == 'TRACE':
        if not config.TRACE_METHOD_ENABLED:
            raise NotImplementedError("TRACE not implemented")
        response = Ok(request)  # TODO serialize request
        response.headers['content-type'] = b'message/http'
        return response

    raise NotFound


class HttpStrategy:
    dispatcher: Dispatcher = staticmethod(default_dispatcher)
    debug = False  # TODO use this instead of global config?

    def session(self, sid) -> Session: ...

    def authenticate(self, request: Request) -> typing.Any:
        ...
        return None

    def wrap_error(self, request: Request, exception: Exception) -> Response:
        if isinstance(exception, Response):
            return exception
        return ServerErrorResponse(exception)

    def new_connection(self) -> HttpConnection:
        return HttpConnection(self)


class Router(Dispatcher):
    def __init__(self):
        self._routes = {}
        # Routes:
        #   *by default static strings* -> Routes
        #   /METHOD -> Dispatcher
        #   :param: ("<"...">") -> Routes
        #   :fallback: ("...") -> Dispatcher

    def route(self, path: str, *, priority: float = 1.0):
        """
        :param method: Method to bind
        :param path: Path pattern
        """
        parts = path.split('/')
        parts.reverse()
        if parts.pop():
            raise ValueError("invalid path")

        routes = self._routes
        while parts:
            part = parts.pop()
            if part == '...':
                if parts:
                    raise ValueError("\"/...\" can only be the last element")
                fallbacks = routes.setdefault(':fallback:', [])
                routes = {}
                fallbacks.append((priority, routes))
                break
            elif part[0] == '<':  # noqa: RET508
                # TODO merge routes?
                params = routes.setdefault(':param:', [])
                routes = {}
                params.append((priority, part, routes))
                routes = routes
            else:
                route = routes.get(part)
                if route is None:
                    routes[part] = route = {}
                routes = route

        return self._RouterPath(routes)

    class _RouterPath:
        def __init__(self, routes):
            self._routes = routes

        def method(self, name):
            def add_method(func):
                self._routes['/' + name] = func
                return func

            return add_method

        get = functools.partialmethod(method, 'GET')
        post = functools.partialmethod(method, 'POST')
        put = functools.partialmethod(method, 'PUT')
        patch = functools.partialmethod(method, 'PATCH')
        delete = functools.partialmethod(method, 'DELETE')
        head = functools.partialmethod(method, 'HEAD')
        options = functools.partialmethod(method, 'OPTIONS')

        def __call__(self, func):
            self._routes['/'] = func
            return func

    async def __dispatch(self, routes: dict, request: Request) -> Response:
        exception = None
        if path_parts := request._path_route:
            try:
                part = path_parts.pop()
                if route := routes.get(part):
                    try:
                        return await self.__dispatch(route, request)
                    except ClientErrorResponse as e:
                        exception = e
                if part == '.' or not part:
                    return await self.__dispatch(routes, request)
                if part == '..':
                    raise NotFound  # path traversal
                for _prio, param_name, routes in routes.get(':param:', ()):
                    old_value = request.path_params.get(param_name)
                    try:
                        request.path_params[param_name] = part
                        return await self.__dispatch(routes, request)
                    except ClientErrorResponse as e:
                        exception = e
                    finally:
                        if old_value is None:
                            request.path_params.pop(param_name, None)
                        else:
                            request.path_params[param_name] = old_value
            finally:
                path_parts.append(part)
        else:
            if func := routes.get('/' + request.method):
                return await func(request)
            if request.method == 'HEAD' and (func := routes.get('/GET')):
                return await self.__head_fallback(request, func)
            if func := routes.get('/'):
                return await func(request)
            if any(r[0] == '/' for r in routes):
                raise MethodNotAllowed(request.method)
        for _prio, routes in routes.get(':fallback:', ()):
            try:
                return await self.__dispatch(routes, request)
            except ClientErrorResponse as e:
                exception = e  # TODO append
        raise exception or NotFound

    async def __call__(self, request: Request) -> Response:
        if request.target == b'*' and not request._path_route:
            return await default_dispatcher(request)
        return await self.__dispatch(self._routes, request)

    async def __head_fallback(self, request, func):
        request.method = 'GET'
        try:
            response = await func(request)
        finally:
            request.method = 'HEAD'
        # remove the body
        response.content = None
        return response


class ContentTypeDispatcher(Dispatcher):
    def __init__(self, default_content_type):
        self.content_types: dict[str, Dispatcher] = {}

    async def __call__(self, request: Request):
        accept = request.accept
        # TODO get most acceptable
        for content_type, func in self.content_types:
            if accept.acceptable(content_type):
                return await func(request)
        raise NotAcceptable


class FileDispatcher(Dispatcher):
    def __init__(self, root_path):
        from pathlib import Path

        self.root_path = Path(root_path)

    async def __call__(self, request):
        # TODO add accept-ranges header
        # TODO add x-accel-redirect
        if request.method != b'GET':
            raise MethodNotAllowed(request.method, [b'GET', b'HEAD'])

        path = self.root_path / request.target.decode()
        try:
            return Response(FileBody(path.open('rb')))
        except FileNotFoundError:
            raise NotFound(path)
