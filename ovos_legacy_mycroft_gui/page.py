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
"""GuiPage — a single QML page entry in the mycroft-gui namespace stack."""
from dataclasses import dataclass


@dataclass
class GuiPage:
    """A single page in a mycroft-gui namespace.

    QML templates are bundled inside the Qt client (mycroft-gui-qt5 /
    ovos-shell), **not** in this Python plugin.  The server emits a
    ``SYSTEM:<name>`` URI; the client resolves it locally via
    ``resolveDelegate()`` in ``abstractskillview.cpp``.

    Args:
        name:       QML file name, e.g. ``"Weather.qml"``.
        namespace:  Skill / component identifier (skill_id).
        persistent: ``True`` to display indefinitely.
        duration:   Seconds to display when not persistent.
    """
    name: str
    namespace: str
    persistent: bool = False
    duration: int = 30

    def get_uri(self, framework: str = "qt5") -> str:  # noqa: ARG002
        """Return the ``SYSTEM:`` URI for this page's template.

        The Qt client resolves ``SYSTEM:<name>`` to a locally installed QML
        file, so no filesystem path is sent over the wire.

        Args:
            framework: Accepted for API compatibility; currently ignored
                       because all system templates are framework-agnostic.

        Returns:
            A ``SYSTEM:`` URI string, e.g. ``"SYSTEM:Weather.qml"``.
        """
        return f"SYSTEM:{self.name}"
