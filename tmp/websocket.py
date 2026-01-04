# https://datatracker.ietf.org/doc/html/rfc6455
from .base_protocol import BaseBufferedProtocol
from .response import UpgradeResponse

class Websocket(BaseBufferedProtocol):
    @classmethod
    def upgrade(headers):
        ws = Websocket(buffer_size=1000)
        # TODO
        return UpgradeResponse(ws, 'websocket')
