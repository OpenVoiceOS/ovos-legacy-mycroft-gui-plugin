# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tornado WebSocket server implementing the mycroft-gui Qt transport protocol.

Protocol reference:
    https://github.com/MycroftAI/mycroft-gui/blob/master/transportProtocol.md

The basic mechanism:
    1. Qt client connects to the OVOS core message bus.
    2. Core replies with the GUI WebSocket port (``mycroft.gui.port`` message).
    3. Qt client opens a WebSocket to this server.
    4. This server syncs namespace state to the new client.
    5. Connection persists for the lifetime of the session.
"""
import asyncio
import json
from threading import Lock
from typing import TYPE_CHECKING, Dict, List, Optional

from ovos_bus_client import GUIMessage, Message
from ovos_config.config import Configuration
from ovos_utils import create_daemon
from ovos_utils.log import LOG
from tornado import ioloop
from tornado.options import parse_command_line
from tornado.web import Application
from tornado.websocket import WebSocketHandler

if TYPE_CHECKING:
    from ovos_legacy_mycroft_gui import LegacyMycoftGuiPlugin

_write_lock = Lock()


def get_gui_websocket_config() -> dict:
    """Return the ``[gui_websocket]`` configuration section."""
    return Configuration().get("gui_websocket", {
        "host": "0.0.0.0",
        "base_port": 18181,
        "route": "/gui",
        "ssl": False,
    })


def create_gui_service(plugin: "LegacyMycoftGuiPlugin") -> Application:
    """Start the Tornado WebSocket server for Qt GUI clients.

    Args:
        plugin: The owning :class:`LegacyMycoftGuiPlugin` instance.  Passed
                into the handler so it can synchronize state on new connections.

    Returns:
        The running Tornado :class:`Application` instance.
    """
    LOG.info("Starting mycroft-gui WebSocket server...")
    cfg = get_gui_websocket_config()
    parse_command_line(["--logging=None"])

    routes = [(cfg.get("route", "/gui"), QtGUIWebSocketHandler)]
    app = Application(routes, gui_plugin=plugin)
    app.listen(cfg.get("base_port", 18181), cfg.get("host", "0.0.0.0"))

    create_daemon(ioloop.IOLoop.instance().start)
    LOG.info(
        f"mycroft-gui WebSocket server started on "
        f"{cfg.get('host')}:{cfg.get('base_port')}{cfg.get('route')}"
    )
    return app


def send_to_all_clients(message: dict):
    """Broadcast *message* (as JSON) to every connected Qt client.

    Args:
        message: Dict conforming to the mycroft-gui transport protocol.
    """
    for client in QtGUIWebSocketHandler.clients:
        try:
            client.send(message)
        except Exception as e:
            LOG.exception(f"Error sending to Qt client: {e}")


def send_to_clients_for_session(session_id: str, message: dict):
    """Send *message* only to Qt clients whose ``session_id`` matches.

    ``"default"`` is the session identifier for the on-device display — it is
    **not** a wildcard; only clients that registered with ``session_id="default"``
    will receive the message. Clients that share a screen (multi-room) share the
    same ``session_id``.

    Args:
        session_id: The target session identifier (e.g. ``"default"`` for the
                    on-device display, or a random UUID for standalone GUIs).
        message: Dict conforming to the mycroft-gui transport protocol.
    """
    for client in QtGUIWebSocketHandler.clients:
        if client.session_id == session_id:
            try:
                client.send(message)
            except Exception as e:
                LOG.exception(f"Error sending to Qt client (session={session_id}): {e}")


def any_client_connected() -> bool:
    """Return ``True`` if at least one Qt client is connected."""
    return len(QtGUIWebSocketHandler.clients) > 0


class QtGUIWebSocketHandler(WebSocketHandler):
    """WebSocket handler for mycroft-gui Qt clients.

    Each Qt window that opens opens one connection here.  On connection the
    server pushes the full current namespace stack so the client is immediately
    up to date.
    """

    clients: List["QtGUIWebSocketHandler"] = []

    def __init__(self, *args, **kwargs):
        WebSocketHandler.__init__(self, *args, **kwargs)
        self._framework = "qt5"
        self._session_id: str = "default"
        self._plugin: "LegacyMycoftGuiPlugin" = self.application.settings["gui_plugin"]

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def framework(self) -> str:
        """Qt framework string reported by this client (``"qt5"`` or ``"qt6"``)."""
        return self._framework

    @property
    def session_id(self) -> str:
        """Session identifier reported by this client (shared screens share one)."""
        return self._session_id

    # ------------------------------------------------------------------
    # WebSocket lifecycle
    # ------------------------------------------------------------------

    def open(self):
        QtGUIWebSocketHandler.clients.append(self)
        LOG.info(f"Qt GUI client connected (total: {len(self.clients)})")
        self._plugin.synchronize(self)

    def on_close(self):
        QtGUIWebSocketHandler.clients.remove(self)
        LOG.info(f"Qt GUI client disconnected (remaining: {len(self.clients)})")

    # ------------------------------------------------------------------
    # Incoming messages from the Qt client → forward to OVOS core bus
    # ------------------------------------------------------------------

    def on_message(self, raw: str):
        """Deserialize *raw* JSON and forward the event to the OVOS core bus."""
        LOG.debug(f"Qt client → server: {raw}")
        try:
            parsed = GUIMessage.deserialize(raw)
        except Exception:
            LOG.exception(f"Failed to deserialize Qt message: {raw}")
            return

        LOG.debug(f"msg_type={parsed.msg_type} data={parsed.data}")

        if parsed.msg_type == "mycroft.events.triggered":
            event_name = parsed.data.get("event_name")
            if event_name in ("page_gained_focus", "system.gui.user.interaction"):
                msg_type = (
                    "gui.page_gained_focus"
                    if event_name == "page_gained_focus"
                    else "gui.page_interaction"
                )
                msg_data = {
                    "namespace": parsed.data.get("namespace"),
                    "page_number": parsed.data.get("parameters", {}).get("number"),
                    "skill_id": parsed.data.get("parameters", {}).get("skillId"),
                }
            else:
                # Generic event — forward as <namespace>.<event_name>
                msg_type = (
                    f"{parsed.data.get('namespace')}."
                    f"{parsed.data.get('event_name')}"
                )
                msg_data = parsed.data.get("parameters", {})

        elif parsed.msg_type == "mycroft.session.set":
            msg_type = f"{parsed.data.get('namespace')}.set"
            msg_data = parsed.data.get("data", {})

        elif parsed.msg_type == "mycroft.gui.connected":
            # Qt client announcing its framework version and session.
            #
            # The client may send:
            #   "session_id": "default"        → on-device GUI (Mark2, laptop)
            #   "session_id": "<session-uuid>" → standalone remote GUI (phone)
            #
            # Clients that share a screen (multi-room) share the same session_id.
            # If omitted, defaults to "default" (on-device).
            default_qt = (
                Configuration().get("gui", {}).get("default_qt_version") or 5
            )
            framework = parsed.data.get("framework")
            if framework is None:
                qt = parsed.data.get("qt_version") or default_qt
                framework = "qt6" if int(qt) == 6 else "qt5"
            self._framework = framework
            session_id = parsed.data.get("session_id") or "default"
            self._session_id = session_id
            LOG.info(f"Qt client identified as {framework}, session_id='{session_id}'")
            msg_type = parsed.msg_type
            msg_data = parsed.data

        else:
            LOG.warning(
                f"Unknown mycroft-gui protocol message type: {parsed.msg_type}"
            )
            return

        parsed.context["gui_framework"] = self.framework
        core_message = Message(msg_type, msg_data, parsed.context)
        LOG.debug("Forwarding to OVOS core bus")
        self._plugin.bus.emit(core_message)

    # ------------------------------------------------------------------
    # Outgoing helpers
    # ------------------------------------------------------------------

    def write_message(self, *args, **kwargs):
        """Thread-safe wrapper around ``WebSocketHandler.write_message``."""
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        with _write_lock:
            super().write_message(*args, **kwargs)

    def send(self, data: dict):
        """Serialize *data* to JSON and write it to this WebSocket."""
        self.write_message(json.dumps(data))

    def send_pages(self, pages, namespace: str, position: int):
        """Send a ``mycroft.gui.list.insert`` for *pages* to this client.

        Args:
            pages:     Iterable of :class:`~ovos_legacy_mycroft_gui.page.GuiPage`.
            namespace: Skill / component identifier.
            position:  Insertion position in the page list.
        """
        self.send({
            "type": "mycroft.gui.list.insert",
            "namespace": namespace,
            "position": position,
            "data": [
                {"url": p.get_uri(self.framework), "page": p.name}
                for p in pages
            ],
        })

    def check_origin(self, origin):
        """Allow cross-origin WebSocket connections (required for JS clients)."""
        return True
