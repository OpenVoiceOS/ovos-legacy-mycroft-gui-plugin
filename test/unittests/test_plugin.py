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
"""Unit tests for the LegacyMycoftGuiPlugin GUI adapter.

These tests never start a real Tornado server: ``create_gui_service`` is
patched out so plugin construction is hermetic. WS routing is verified by
mocking the client registry.
"""
import inspect
import unittest
from unittest.mock import MagicMock, patch

from ovos_plugin_manager.templates.gui import AbstractGUIPlugin


def _make_plugin():
    """Instantiate the plugin without binding a real WebSocket server."""
    with patch("ovos_legacy_mycroft_gui.create_gui_service") as mock_create:
        from ovos_legacy_mycroft_gui import LegacyMycoftGuiPlugin
        plugin = LegacyMycoftGuiPlugin(config={}, bus=None)
        mock_create.assert_called_once_with(plugin)
    return plugin


class TestPluginContract(unittest.TestCase):
    def test_is_abstract_gui_plugin(self):
        plugin = _make_plugin()
        self.assertIsInstance(plugin, AbstractGUIPlugin)

    def test_entry_point_registered(self):
        from ovos_plugin_manager.utils import PluginTypes  # noqa: F401  (import smoke)
        try:
            from importlib.metadata import entry_points
        except ImportError:  # pragma: no cover - py<3.8 only
            from importlib_metadata import entry_points
        eps = entry_points()
        if hasattr(eps, "select"):
            group = eps.select(group="opm.gui_adapter")
        else:  # pragma: no cover - legacy API
            group = eps.get("opm.gui_adapter", [])
        names = {ep.name for ep in group}
        self.assertIn("ovos-legacy-mycroft-gui", names)

    def test_hooks_expose_session_id_not_site_id(self):
        """Every lifecycle hook / handler must use session_id and never site_id."""
        plugin = _make_plugin()
        hooks = [
            "on_namespace_activated",
            "on_namespace_deactivated",
            "on_session_update",
            "on_status_event",
        ]
        handlers = [n for n in dir(plugin) if n.startswith("handle_show_")]
        for name in hooks + handlers:
            params = list(inspect.signature(getattr(plugin, name)).parameters)
            self.assertNotIn("site_id", params,
                             f"{name} still exposes site_id")
            self.assertIn("session_id", params,
                          f"{name} is missing session_id")


class TestDispatchTemplate(unittest.TestCase):
    def test_dispatch_routes_to_handler(self):
        plugin = _make_plugin()
        with patch.object(plugin, "handle_show_text") as mock_handler:
            plugin.dispatch_template("SYSTEM_text", "skill.test", {"text": "hi"})
            mock_handler.assert_called_once_with("skill.test", {"text": "hi"}, "default")

    def test_dispatch_unknown_template_is_noop(self):
        plugin = _make_plugin()
        # Must not raise
        plugin.dispatch_template("SYSTEM_does_not_exist", "skill.test", {})


class TestWebSocketRouting(unittest.TestCase):
    def test_broadcast_helper_targets_by_session_id(self):
        from ovos_legacy_mycroft_gui import websocket

        target = MagicMock()
        target.session_id = "living-room"
        other = MagicMock()
        other.session_id = "default"

        with patch.object(websocket.QtGUIWebSocketHandler, "clients",
                          [target, other]):
            websocket.send_to_clients_for_session("living-room", {"type": "x"})

        target.send.assert_called_once_with({"type": "x"})
        other.send.assert_not_called()

    def test_show_template_targets_session(self):
        plugin = _make_plugin()
        with patch("ovos_legacy_mycroft_gui.send_to_clients_for_session") as mock_send:
            plugin.handle_show_text("skill.test", {"text": "hi"},
                                    session_id="living-room")
        self.assertTrue(mock_send.called)
        for call in mock_send.call_args_list:
            self.assertEqual(call.args[0], "living-room")


if __name__ == "__main__":
    unittest.main()
