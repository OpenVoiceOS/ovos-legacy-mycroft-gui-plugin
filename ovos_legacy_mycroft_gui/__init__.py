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
"""ovos-legacy-mycroft-gui-plugin

Translates the new OVOS template API (``SYSTEM_*`` page templates) into the
mycroft-gui Qt WebSocket protocol so that existing Qt5/Qt6 GUI clients can
display skill output without any skill-side QML.

Skills no longer ship QML files.  All rendering is done by this plugin's
bundled ``ui/*.qml`` pages.

Entry point::

    opm.gui_adapter = ovos_legacy_mycroft_gui:LegacyMycoftGuiPlugin
"""
from threading import Lock
from typing import Any, Dict, List

from ovos_bus_client import Message
from ovos_plugin_manager.templates.gui import AbstractGUIPlugin
from ovos_utils.log import LOG

from ovos_legacy_mycroft_gui.homescreen import HomescreenManager
from ovos_legacy_mycroft_gui.page import GuiPage
from ovos_legacy_mycroft_gui.websocket import (
    QtGUIWebSocketHandler,
    create_gui_service,
    get_gui_websocket_config,
    send_to_all_clients,
    send_to_clients_for_site,
)

# Session-data keys never forwarded to Qt clients
_RESERVED = {"__from", "__idle", "__animations"}

# Maps every SYSTEM_* template identifier to the bundled QML file name
_TEMPLATE_QML: Dict[str, str] = {
    # --- Official GUI spec templates ---
    "SYSTEM_idle":           "Idle.qml",
    "SYSTEM_loading":        "Loading.qml",
    "SYSTEM_status":         "Status.qml",
    "SYSTEM_error":          "Error.qml",
    "SYSTEM_text":           "Text.qml",
    "SYSTEM_image":          "Image.qml",
    "SYSTEM_animated_image": "AnimatedImage.qml",
    "SYSTEM_list":           "List.qml",
    "SYSTEM_grid":           "Grid.qml",
    "SYSTEM_table":          "Table.qml",
    "SYSTEM_html":           "Html.qml",
    "SYSTEM_url":            "Url.qml",
    "SYSTEM_audio_player":   "AudioPlayer.qml",
    "SYSTEM_video_player":   "VideoPlayer.qml",
    "SYSTEM_media_player":   "MediaPlayer.qml",
    "SYSTEM_clock":          "Clock.qml",
    "SYSTEM_timer":          "Timer.qml",
    "SYSTEM_weather":        "Weather.qml",
    "SYSTEM_map":            "Map.qml",
    "SYSTEM_confirm":        "Confirm.qml",
    "SYSTEM_select":         "Select.qml",
    "SYSTEM_face":           "Face.qml",
    # --- OCP GUI spec templates ---
    "SYSTEM_ocp_now_playing": "OCPNowPlaying.qml",
    "SYSTEM_ocp_search":      "OCPSearch.qml",
    "SYSTEM_ocp_playlist":    "OCPPlaylist.qml",
}


class _NamespaceState:
    """Per-namespace state mirrored on the Qt client side."""

    def __init__(self, skill_id: str):
        self.skill_id = skill_id
        self.data: Dict[str, Any] = {}
        self.page: str = ""          # current QML file name shown


class LegacyMycoftGuiPlugin(AbstractGUIPlugin):
    """Translates OVOS template events into the mycroft-gui WebSocket protocol.

    On startup this plugin:

    1. Starts the Tornado WebSocket server (default port 18181).
    2. Registers a ``mycroft.gui.connected`` handler so Qt clients receive the
       WebSocket port.

    For every template event received through the :class:`AbstractGUIPlugin`
    interface it:

    - Syncs session data to Qt clients via ``mycroft.session.set``.
    - Shows the matching bundled QML page via ``mycroft.gui.list.insert`` /
      ``mycroft.events.triggered`` (``page_gained_focus``).
    - Manages the namespace stack via ``mycroft.session.list.*`` messages.
    """

    def __init__(self, config: Dict[str, Any], bus=None):
        super().__init__(config, bus)

        self._lock = Lock()
        # skill_id → _NamespaceState
        self._namespaces: Dict[str, _NamespaceState] = {}
        # LIFO stack of active skill_ids; index 0 = currently visible
        self._active_stack: List[str] = []

        self._ws_app = create_gui_service(self)

        if self.bus:
            self.bus.on("mycroft.gui.connected", self._on_qt_client_announced)
            self.bus.on("mycroft.device.show.idle", self._on_show_idle)
            self._homescreen = HomescreenManager(self.bus)
        else:
            self._homescreen = None

        LOG.info("LegacyMycoftGuiPlugin ready")

    # ------------------------------------------------------------------
    # Qt client port negotiation
    # ------------------------------------------------------------------

    def _on_show_idle(self, message: Message):
        """Clear all active namespaces so the shell falls back to its native homescreen."""
        with self._lock:
            stack_copy = list(self._active_stack)
        for skill_id in stack_copy:
            self._pop_namespace(skill_id)

    def _on_qt_client_announced(self, message: Message):
        """Reply with the GUI WebSocket port when a Qt client announces itself on the core bus."""
        gui_id = message.data.get("gui_id")
        framework = message.data.get("framework")
        if framework is None:
            qt = message.data.get("qt_version") or self.config.get("default_qt_version", 5)
            framework = "qt6" if int(qt) == 6 else "qt5"

        port = get_gui_websocket_config().get("base_port", 18181)
        self.bus.emit(message.forward(
            "mycroft.gui.port",
            {"port": port, "gui_id": gui_id, "framework": framework},
        ))
        LOG.info(f"Sent GUI WebSocket port {port} to Qt client {gui_id} ({framework})")

    # ------------------------------------------------------------------
    # Internal namespace stack management
    # ------------------------------------------------------------------

    def _ensure_namespace(self, skill_id: str) -> _NamespaceState:
        if skill_id not in self._namespaces:
            self._namespaces[skill_id] = _NamespaceState(skill_id)
        return self._namespaces[skill_id]

    def _push_namespace(self, skill_id: str, site_id: str = "default"):
        """Move *skill_id* to the top of the active stack on Qt clients."""
        with self._lock:
            if skill_id in self._active_stack:
                pos = self._active_stack.index(skill_id)
                if pos == 0:
                    return
                self._active_stack.insert(0, self._active_stack.pop(pos))
                send_to_clients_for_site(site_id, {
                    "type": "mycroft.session.list.move",
                    "namespace": "mycroft.system.active_skills",
                    "from": pos,
                    "to": 0,
                    "items_number": 1,
                })
            else:
                self._active_stack.insert(0, skill_id)
                send_to_clients_for_site(site_id, {
                    "type": "mycroft.session.list.insert",
                    "namespace": "mycroft.system.active_skills",
                    "position": 0,
                    "data": [{"skill_id": skill_id}],
                })

    def _pop_namespace(self, skill_id: str):
        """Remove *skill_id* from the active stack on all Qt clients."""
        with self._lock:
            if skill_id not in self._active_stack:
                return
            pos = self._active_stack.index(skill_id)
            self._active_stack.remove(skill_id)

        # Namespace removal broadcasts to all sites (skill is gone globally)
        send_to_all_clients({
            "type": "mycroft.session.list.remove",
            "namespace": "mycroft.system.active_skills",
            "position": pos,
            "items_number": 1,
        })

    def _sync_session_data(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        """Forward session data to the appropriate Qt clients as ``mycroft.session.set`` messages."""
        for key, value in data.items():
            if key in _RESERVED:
                continue
            send_to_clients_for_site(site_id, {
                "type": "mycroft.session.set",
                "namespace": skill_id,
                "data": {key: value},
            })

    def _show_qml(self, skill_id: str, qml_name: str, site_id: str = "default"):
        """Insert *qml_name* as the single page in *skill_id*'s namespace and focus it."""
        page = GuiPage(name=qml_name, namespace=skill_id, persistent=True)

        # Always replace — only one page per namespace in the new model
        send_to_clients_for_site(site_id, {
            "type": "mycroft.gui.list.insert",
            "namespace": skill_id,
            "position": 0,
            "data": [{"url": page.get_uri(), "page": qml_name}],
        })
        send_to_clients_for_site(site_id, {
            "type": "mycroft.events.triggered",
            "namespace": skill_id,
            "event_name": "page_gained_focus",
            "data": {"number": 0},
        })

        ns = self._ensure_namespace(skill_id)
        ns.page = qml_name

    def _show_template(self, template_id: str, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        """Translate one SYSTEM_* template event into mycroft-gui protocol messages.

        1. Sync session data so QML properties are up to date.
        2. Push namespace to the top of the Qt stack.
        3. Insert the matching bundled QML page.
        """
        qml_name = _TEMPLATE_QML.get(template_id)
        if not qml_name:
            LOG.warning(f"No QML mapping for template '{template_id}'")
            return

        ns = self._ensure_namespace(skill_id)
        ns.data.update({k: v for k, v in data.items() if k not in _RESERVED})

        self._sync_session_data(skill_id, ns.data, site_id)
        self._push_namespace(skill_id, site_id)
        self._show_qml(skill_id, qml_name, site_id)

    # ------------------------------------------------------------------
    # Synchronize state to a newly connected Qt client
    # ------------------------------------------------------------------

    def synchronize(self, client: "QtGUIWebSocketHandler"):
        """Push full namespace state to a freshly connected Qt client.

        Called by :class:`~ovos_legacy_mycroft_gui.websocket.QtGUIWebSocketHandler`
        immediately after ``open()``.
        """
        with self._lock:
            for idx, skill_id in enumerate(self._active_stack):
                ns = self._namespaces.get(skill_id)
                if not ns:
                    continue

                client.send({
                    "type": "mycroft.session.list.insert",
                    "namespace": "mycroft.system.active_skills",
                    "position": idx,
                    "data": [{"skill_id": skill_id}],
                })

                if ns.page:
                    page = GuiPage(name=ns.page, namespace=skill_id)
                    client.send({
                        "type": "mycroft.gui.list.insert",
                        "namespace": skill_id,
                        "position": 0,
                        "data": [{"url": page.get_uri(client.framework), "page": ns.page}],
                    })

                for key, value in ns.data.items():
                    if key not in _RESERVED:
                        client.send({
                            "type": "mycroft.session.set",
                            "namespace": skill_id,
                            "data": {key: value},
                        })

    # ------------------------------------------------------------------
    # AbstractGUIPlugin — lifecycle hooks
    # ------------------------------------------------------------------

    def on_namespace_activated(self, skill_id: str, site_id: str = "default"):
        self._push_namespace(skill_id, site_id)

    def on_namespace_deactivated(self, skill_id: str, site_id: str = "default"):
        ns = self._namespaces.pop(skill_id, None)
        if ns:
            ns.data.clear()
        self._pop_namespace(skill_id)

    def on_session_update(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        ns = self._ensure_namespace(skill_id)
        with self._lock:
            ns.data.update({k: v for k, v in data.items() if k not in _RESERVED})
        # Only push live if this namespace is currently visible
        if self._active_stack and self._active_stack[0] == skill_id:
            self._sync_session_data(skill_id, data, site_id)

    def on_status_event(self, event_name: str, data: Dict[str, Any], site_id: str = "default"):
        """Forward OVOS status events to Qt clients as ``system``-namespace triggers.

        Status events (wakeword, speaking, etc.) are system-wide — sent to
        every connected Qt client regardless of site.
        """
        send_to_all_clients({
            "type": "mycroft.events.triggered",
            "namespace": "system",
            "event_name": event_name,
            "data": data,
        })

    # ------------------------------------------------------------------
    # AbstractGUIPlugin — template handlers (all delegate to _show_template)
    # ------------------------------------------------------------------

    def handle_show_idle(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_idle", skill_id, data, site_id)

    def handle_show_loading(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_loading", skill_id, data, site_id)

    def handle_show_status(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_status", skill_id, data, site_id)

    def handle_show_error(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_error", skill_id, data, site_id)

    def handle_show_text(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_text", skill_id, data, site_id)

    def handle_show_image(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_image", skill_id, data, site_id)

    def handle_show_animated_image(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_animated_image", skill_id, data, site_id)

    def handle_show_list(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_list", skill_id, data, site_id)

    def handle_show_grid(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_grid", skill_id, data, site_id)

    def handle_show_table(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_table", skill_id, data, site_id)

    def handle_show_html(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_html", skill_id, data, site_id)

    def handle_show_url(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_url", skill_id, data, site_id)

    def handle_show_audio_player(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_audio_player", skill_id, data, site_id)

    def handle_show_video_player(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_video_player", skill_id, data, site_id)

    def handle_show_media_player(
        self,
        skill_id: str,
        data: Dict[str, Any],
        site_id: str = "default",
    ) -> None:
        """Render the OCP unified media player (now-playing, playlist, search)."""
        self._show_template("SYSTEM_media_player", skill_id, data, site_id)

    def handle_show_clock(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_clock", skill_id, data, site_id)

    def handle_show_timer(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_timer", skill_id, data, site_id)

    def handle_show_weather(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_weather", skill_id, data, site_id)

    def handle_show_map(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_map", skill_id, data, site_id)

    def handle_show_confirm(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_confirm", skill_id, data, site_id)

    def handle_show_select(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_select", skill_id, data, site_id)

    def handle_show_face(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_face", skill_id, data, site_id)

    # ------------------------------------------------------------------
    # OCP spec template handlers
    # ------------------------------------------------------------------

    def handle_show_ocp_now_playing(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_ocp_now_playing", skill_id, data, site_id)

    def handle_show_ocp_search(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_ocp_search", skill_id, data, site_id)

    def handle_show_ocp_playlist(self, skill_id: str, data: Dict[str, Any], site_id: str = "default"):
        self._show_template("SYSTEM_ocp_playlist", skill_id, data, site_id)
