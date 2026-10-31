"""Discord desktop RPC: the locally signed-in account, without OAuth tokens."""
import asyncio
import copy
import os
import threading
import time
import uuid
from pypresence import Presence

# Public application identifier, not a credential or an account/server identifier.
DEFAULT_APPLICATION_ID = "1559806036645515274"


class DesktopPresence:
    def __init__(self, client_id=DEFAULT_APPLICATION_ID):
        self.client_id = client_id
        self.rpc = None
        self.loop = None
        self.last = None
        self.retry_at = 0
        self.lock = threading.RLock()
        self._status = {"state":"not_connected", "message":"Open Discord desktop and sign in on this computer."}

    @property
    def status(self):
        return dict(self._status)

    def _connect(self):
        self.loop = asyncio.new_event_loop()
        self.rpc = Presence(self.client_id, loop=self.loop, connection_timeout=4, response_timeout=4)
        self.loop.run_until_complete(asyncio.wait_for(self.rpc.handshake(), timeout=5))

    def _send(self, activity):
        self.rpc.send_data(1, {"cmd":"SET_ACTIVITY", "args":{"pid":os.getpid(), "activity":activity},
                               "nonce":uuid.uuid4().hex})
        self.loop.run_until_complete(asyncio.wait_for(self.rpc.read_output(), timeout=5))

    def close(self):
        with self.lock:
            if self.rpc:
                try:
                    writer = self.rpc.sock_writer
                    if writer:
                        writer.close()
                        self.loop.run_until_complete(asyncio.sleep(0))
                except Exception:
                    pass
            if self.loop and not self.loop.is_closed():
                self.loop.close()
            self.rpc = self.loop = None
            self.last = None

    def sync(self, activity):
        with self.lock:
            if time.monotonic() < self.retry_at:
                return self.status
            try:
                if self.rpc is None:
                    self._connect()
                if activity != self.last:
                    self._send(activity)
                    self.last = copy.deepcopy(activity)
                self._status = {"state":"active" if activity else "idle",
                    "message":"Connected to Discord desktop; sharing playback." if activity else "Discord desktop connected; waiting for playback."}
            except Exception:
                self.close()
                self.retry_at = time.monotonic() + 5
                self._status = {"state":"not_connected", "message":"Could not reach Discord desktop. Open Discord, sign in, and keep it running on this computer. Retrying automatically."}
            return self.status
