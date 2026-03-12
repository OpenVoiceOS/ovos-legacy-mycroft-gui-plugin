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
"""HomescreenManager — data broker between the OVOS bus and the Qt homescreen.

This class takes over the responsibilities that ``ovos-skill-homescreen`` used
to handle.  It subscribes to the relevant bus events, aggregates data (datetime,
weather, wallpaper, notifications, apps, examples, connectivity, widgets), and
re-emits everything as ``homescreen.data.*`` / ``homescreen.widget.*`` bus
events consumed by ``HomescreenController.qml`` in ovos-shell.

Bus events emitted
------------------
homescreen.data.time           { time_string, date_string, weekday_string,
                                  day_string, month_string, year_string }
homescreen.data.weather        { weather_code, weather_temp, weather_api_enabled }
homescreen.data.wallpaper      { wallpaper_path, selected_wallpaper }
homescreen.data.notifications  { notification_counter, notification_model }
homescreen.data.apps           { applications_model }
homescreen.data.examples       { skill_examples, skill_info_enabled, skill_info_prefix }
homescreen.data.connectivity   { system_connectivity }
homescreen.widget.timer        { count, ...widget fields... }
homescreen.widget.alarm        { count, ...widget fields... }
homescreen.widget.media        { enabled, widget, state }
"""

import random
import threading
from typing import Dict, List, Optional, Tuple

from ovos_bus_client import Message
from ovos_config.config import Configuration
from ovos_date_parser import get_date_strings
from ovos_utils.lang import standardize_lang_tag
from ovos_utils.log import LOG
from ovos_utils.time import now_local


class HomescreenManager:
    """Manages homescreen data and emits bus events consumed by the Qt client.

    Instantiated by :class:`~ovos_legacy_mycroft_gui.LegacyMycoftGuiPlugin`
    after the bus connection is established.
    """

    # How often (seconds) to push fresh datetime to the homescreen
    _DT_INTERVAL = 10
    # How often (seconds) to request a fresh weather update
    _WEATHER_INTERVAL = 900
    # How often (seconds) to rotate example utterances
    _EXAMPLES_INTERVAL = 900

    def __init__(self, bus):
        self.bus = bus

        # Cached state
        self._wallpaper_path: str = ""
        self._selected_wallpaper: str = ""
        self._notification_counter: int = 0
        self._notification_model: list = []
        self._system_connectivity: str = "offline"
        self._media_player_state: Optional[str] = None

        # "skill_id": {"icon": ..., "event": ..., "name": ...}
        self._homescreen_apps: Dict[str, Dict[str, str]] = {}
        # "skill_id": {"lang-code": ["utterance", ...]}
        self._skill_examples: Dict[str, Dict[str, List[str]]] = {}

        self._timers: List[threading.Timer] = []

        self._register_handlers()
        self._start_periodic_tasks()

        # Ask any already-loaded skills to re-register their metadata
        self.bus.emit(Message("homescreen.metadata.get"))
        LOG.info("HomescreenManager started")

    # ------------------------------------------------------------------
    # Bus handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self):
        # Metadata registration
        self.bus.on("homescreen.register.examples", self._handle_register_examples)
        self.bus.on("homescreen.register.app", self._handle_register_app)
        self.bus.on("detach_skill", self._handle_deregister_skill)

        # Wallpaper
        self.bus.on("homescreen.wallpaper.set", self._handle_set_wallpaper)

        # Notifications
        self.bus.on("ovos.notification.update_counter", self._handle_notification_counter)
        self.bus.on("ovos.notification.update_storage_model", self._handle_notification_model)

        # Weather
        self.bus.on("skill-ovos-weather.openvoiceos.weather.response", self._handle_weather_response)

        # Connectivity
        self.bus.on("mycroft.network.connected", self._on_network_connected)
        self.bus.on("mycroft.internet.connected", self._on_internet_connected)
        self.bus.on("enclosure.notify.no_internet", self._on_no_internet)

        # Timer/alarm widgets
        self.bus.on("ovos.widgets.timer.update", self._handle_timer_widget)
        self.bus.on("ovos.widgets.timer.display", self._handle_timer_widget)
        self.bus.on("ovos.widgets.timer.remove", self._handle_timer_widget)
        self.bus.on("ovos.widgets.alarm.update", self._handle_alarm_widget)
        self.bus.on("ovos.widgets.alarm.display", self._handle_alarm_widget)
        self.bus.on("ovos.widgets.alarm.remove", self._handle_alarm_widget)

        # OCP media widget
        self.bus.on("gui.player.media.service.sync.status", self._handle_media_state)
        self.bus.on("ovos.common_play.track_info.response", self._handle_media_track_info)

        # System ready — re-push all cached data so the homescreen is current
        self.bus.on("mycroft.ready", self._on_mycroft_ready)

    # ------------------------------------------------------------------
    # Periodic tasks
    # ------------------------------------------------------------------

    def _start_periodic_tasks(self):
        self._schedule(0, self._push_datetime)
        self._schedule(5, self._request_weather)
        self._schedule(5, self._push_examples)

    def _schedule(self, delay: float, fn):
        """Schedule *fn* to run after *delay* seconds (daemon thread)."""
        t = threading.Timer(delay, fn)
        t.daemon = True
        t.start()
        self._timers.append(t)

    def _reschedule(self, interval: float, fn):
        self._schedule(interval, fn)

    # ------------------------------------------------------------------
    # Datetime
    # ------------------------------------------------------------------

    def _push_datetime(self):
        try:
            config = Configuration()
            lang = config.get("lang", "en-US")
            date_format = config.get("date_format", "DMY")
            time_format = config.get("time_format", "full")

            parts = get_date_strings(
                dt=now_local(),
                date_format=date_format,
                time_format=time_format,
                lang=lang,
            )
            self.bus.emit(Message("homescreen.data.time", {
                "time_string":    parts.get("time_string", ""),
                "date_string":    parts.get("date_string", ""),
                "weekday_string": parts.get("weekday_string", ""),
                "day_string":     parts.get("day_string", ""),
                "month_string":   parts.get("month_string", ""),
                "year_string":    parts.get("year_string", ""),
            }))
        except Exception as e:
            LOG.exception(f"HomescreenManager: datetime update failed: {e}")
        finally:
            self._reschedule(self._DT_INTERVAL, self._push_datetime)

    # ------------------------------------------------------------------
    # Weather
    # ------------------------------------------------------------------

    def _request_weather(self):
        self.bus.emit(Message("skill-ovos-weather.openvoiceos.weather.request"))
        self._reschedule(self._WEATHER_INTERVAL, self._request_weather)

    def _handle_weather_response(self, message: Message):
        report = message.data.get("report")
        if report:
            self.bus.emit(Message("homescreen.data.weather", {
                "weather_api_enabled": True,
                "weather_code": report.get("weather_code"),
                "weather_temp": report.get("weather_temp"),
            }))
        else:
            self.bus.emit(Message("homescreen.data.weather", {
                "weather_api_enabled": False,
                "weather_code": 0,
                "weather_temp": "",
            }))

    # ------------------------------------------------------------------
    # Wallpaper
    # ------------------------------------------------------------------

    def _handle_set_wallpaper(self, message: Message):
        url = message.data.get("url", "")
        self._wallpaper_path, self._selected_wallpaper = self._split_wallpaper(url)
        self.bus.emit(Message("homescreen.data.wallpaper", {
            "wallpaper_path":    self._wallpaper_path,
            "selected_wallpaper": self._selected_wallpaper,
        }))

    @staticmethod
    def _split_wallpaper(wallpaper: str) -> Tuple[str, str]:
        parts = wallpaper.rsplit("/", 1)
        if len(parts) == 2:
            return parts[0] + "/", parts[1]
        return "", wallpaper

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _handle_notification_counter(self, message: Message):
        self._notification_counter = message.data.get("notification_counter", 0)
        # Request the full storage model so we have up-to-date items
        self.bus.emit(Message("ovos.notification.api.request.storage.model"))
        self.bus.emit(Message("homescreen.data.notifications", {
            "notification_counter": self._notification_counter,
            "notification_model":   self._notification_model,
        }))

    def _handle_notification_model(self, message: Message):
        self._notification_model = message.data.get("notification_model", [])
        self.bus.emit(Message("homescreen.data.notifications", {
            "notification_counter": self._notification_counter,
            "notification_model":   self._notification_model,
        }))

    # ------------------------------------------------------------------
    # Apps drawer
    # ------------------------------------------------------------------

    def _handle_register_app(self, message: Message):
        skill_id = message.data.get("skill_id")
        if not skill_id:
            return
        self._homescreen_apps[skill_id] = {
            "icon":  message.data.get("icon", ""),
            "event": message.data.get("event", ""),
            "name":  message.data.get("name", skill_id),
        }
        LOG.info(f"HomescreenManager: registered app from {skill_id}")
        self._push_apps()

    def _push_apps(self):
        apps = [
            {"name": d["name"], "thumbnail": d["icon"], "action": d["event"]}
            for d in self._homescreen_apps.values()
        ]
        self.bus.emit(Message("homescreen.data.apps", {"applications_model": apps}))

    # ------------------------------------------------------------------
    # Example utterances
    # ------------------------------------------------------------------

    def _handle_register_examples(self, message: Message):
        lang = standardize_lang_tag(message.data.get("lang", "en-US"))
        skill_id = message.data.get("skill_id")
        utterances = message.data.get("utterances", [])
        if skill_id:
            if skill_id not in self._skill_examples:
                self._skill_examples[skill_id] = {}
            self._skill_examples[skill_id][lang] = utterances
            LOG.info(f"HomescreenManager: registered examples from {skill_id}")
            self._push_examples()

    def _push_examples(self):
        try:
            config = Configuration()
            lang = standardize_lang_tag(config.get("lang", "en-US"))
            examples_enabled = config.get("homescreen", {}).get("examples_enabled", True)
            examples_prefix = config.get("homescreen", {}).get("examples_prefix", False)
            randomize = config.get("homescreen", {}).get("randomize_examples", True)

            examples: List[str] = []
            for skill_data in self._skill_examples.values():
                examples.extend(skill_data.get(lang, []))
            examples = [e for e in examples if e.strip()]

            if examples and randomize:
                random.shuffle(examples)

            self.bus.emit(Message("homescreen.data.examples", {
                "skill_examples":   {"examples": examples},
                "skill_info_enabled": examples_enabled and bool(examples),
                "skill_info_prefix": examples_prefix,
            }))
        except Exception as e:
            LOG.exception(f"HomescreenManager: examples update failed: {e}")
        finally:
            self._reschedule(self._EXAMPLES_INTERVAL, self._push_examples)

    # ------------------------------------------------------------------
    # Skill deregistration
    # ------------------------------------------------------------------

    def _handle_deregister_skill(self, message: Message):
        skill_id = message.data.get("skill_id")
        changed = False
        if skill_id in self._skill_examples:
            self._skill_examples.pop(skill_id)
            changed = True
        if skill_id in self._homescreen_apps:
            self._homescreen_apps.pop(skill_id)
            self._push_apps()
            changed = False  # apps already pushed
        if changed:
            self._push_examples()

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def _on_network_connected(self, message: Message):
        self._system_connectivity = "network"
        self._push_connectivity()

    def _on_internet_connected(self, message: Message):
        self._system_connectivity = "online"
        self._push_connectivity()

    def _on_no_internet(self, message: Message):
        self._system_connectivity = "offline"
        self._push_connectivity()

    def _push_connectivity(self):
        self.bus.emit(Message("homescreen.data.connectivity", {
            "system_connectivity": self._system_connectivity,
        }))

    # ------------------------------------------------------------------
    # Timer widget
    # ------------------------------------------------------------------

    def _handle_timer_widget(self, message: Message):
        widget = message.data.get("widget", {})
        count = message.data.get("count", widget.get("count", 0))
        self.bus.emit(Message("homescreen.widget.timer", {
            "count": count,
            **widget,
        }))

    # ------------------------------------------------------------------
    # Alarm widget
    # ------------------------------------------------------------------

    def _handle_alarm_widget(self, message: Message):
        widget = message.data.get("widget", {})
        count = message.data.get("count", widget.get("count", 0))
        self.bus.emit(Message("homescreen.widget.alarm", {
            "count": count,
            **widget,
        }))

    # ------------------------------------------------------------------
    # Media widget (OCP)
    # ------------------------------------------------------------------

    def _handle_media_state(self, message: Message):
        state = message.data.get("state")
        if state == 1:
            self._media_player_state = "playing"
            self.bus.emit(Message("ovos.common_play.track_info"))
            self.bus.emit(Message("homescreen.widget.media", {
                "enabled": True,
                "widget": {},
                "state": "playing",
            }))
        elif state == 2:
            self._media_player_state = "paused"
            self.bus.emit(Message("ovos.common_play.track_info"))
            self.bus.emit(Message("homescreen.widget.media", {
                "enabled": True,
                "widget": {},
                "state": "paused",
            }))
        else:
            self._media_player_state = "stopped"
            self.bus.emit(Message("homescreen.widget.media", {
                "enabled": False,
                "widget": {},
                "state": "stopped",
            }))

    def _handle_media_track_info(self, message: Message):
        self.bus.emit(Message("homescreen.widget.media", {
            "enabled": True,
            "widget": message.data,
            "state": self._media_player_state or "playing",
        }))

    # ------------------------------------------------------------------
    # Re-push all cached state (e.g. after mycroft.ready)
    # ------------------------------------------------------------------

    def _on_mycroft_ready(self, message: Message):
        self._push_apps()
        self._push_connectivity()
        if self._wallpaper_path or self._selected_wallpaper:
            self.bus.emit(Message("homescreen.data.wallpaper", {
                "wallpaper_path":    self._wallpaper_path,
                "selected_wallpaper": self._selected_wallpaper,
            }))
        if self._notification_model or self._notification_counter:
            self.bus.emit(Message("homescreen.data.notifications", {
                "notification_counter": self._notification_counter,
                "notification_model":   self._notification_model,
            }))

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self):
        for t in self._timers:
            t.cancel()
        self._timers.clear()
        LOG.info("HomescreenManager stopped")
