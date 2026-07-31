# HomescreenManager

`HomescreenManager` (`ovos_legacy_mycroft_gui/homescreen.py`) handles the homescreen
data that `ovos-skill-homescreen` used to handle. `LegacyMycoftGuiPlugin` creates
one `HomescreenManager` instance when the bus connection is available.

---

## Responsibilities

- Emit `homescreen.data.*` and `homescreen.widget.*` bus events at the correct
  intervals, so the Qt shell (`ovos-shell`) can show a live idle screen without
  depending on any skill.
- Register and remove apps and example utterances as skills load and unload.
- Track OCP media state for the media widget.

---

## Bus events emitted

### `homescreen.data.time`

Emitted every **10 seconds** using `ovos_date_parser.get_date_strings()`.

```json
{
  "time_string":    "14:35",
  "date_string":    "Friday, 7 March 2026",
  "weekday_string": "Friday",
  "day_string":     "7",
  "month_string":   "March",
  "year_string":    "2026"
}
```

`mycroft.conf` sets the language and the format (`date_format`, `time_format`).

---

### `homescreen.data.weather`

Requested every **900 seconds** through `skill-ovos-weather.openvoiceos.weather.request`.
The response arrives on `skill-ovos-weather.openvoiceos.weather.response`.

```json
{ "weather_api_enabled": true, "weather_code": 1, "weather_temp": "22" }
```

If no weather skill is installed, or the response has no `report` field, the manager
emits this instead:

```json
{ "weather_api_enabled": false, "weather_code": 0, "weather_temp": "" }
```

---

### `homescreen.data.wallpaper`

Emitted on `homescreen.wallpaper.set` (the PHAL wallpaper manager API).

```json
{ "wallpaper_path": "/usr/share/wallpapers/", "selected_wallpaper": "default.jpg" }
```

---

### `homescreen.data.notifications`

Emitted on `ovos.notification.update_counter` (this event also triggers a storage
model request) and on `ovos.notification.update_storage_model`.

```json
{ "notification_counter": 3, "notification_model": { "storedmodel": [...] } }
```

---

### `homescreen.data.apps`

Emitted when a skill registers (`homescreen.register.app`) or unregisters
(`detach_skill`).

```json
{
  "applications_model": [
    { "name": "Timer", "thumbnail": "/path/icon.png", "action": "mycroft.timer.start" }
  ]
}
```

Skills register apps by emitting this event:
```json
{
  "type": "homescreen.register.app",
  "data": { "skill_id": "ovos-skill-timer", "icon": "...", "event": "...", "name": "Timer" }
}
```

---

### `homescreen.data.examples`

Emitted on `homescreen.register.examples`, on `detach_skill`, and every
**900 seconds** to rotate the displayed list.

```json
{
  "skill_examples":    { "examples": ["What time is it?", "Tell me a joke"] },
  "skill_info_enabled": true,
  "skill_info_prefix":  false
}
```

`skill_info_enabled` and `skill_info_prefix` come from `mycroft.conf`, under
`homescreen.examples_enabled` and `homescreen.examples_prefix`.

Skills register examples by emitting this event:
```json
{
  "type": "homescreen.register.examples",
  "data": { "skill_id": "ovos-skill-jokes", "lang": "en-US", "utterances": ["Tell me a joke", "Joke please"] }
}
```

---

### `homescreen.data.connectivity`

Emitted on `mycroft.network.connected`, `mycroft.internet.connected`, and
`enclosure.notify.no_internet`.

```json
{ "system_connectivity": "online" }
```

Possible values: `"online"`, `"network"` (LAN only), `"offline"`.

---

### `homescreen.widget.timer`

Emitted on `ovos.widgets.timer.update`, `.display`, and `.remove`.

```json
{ "count": 2, "timers": [...] }
```

---

### `homescreen.widget.alarm`

Emitted on `ovos.widgets.alarm.update`, `.display`, and `.remove`.

```json
{ "count": 1, "alarms": [...] }
```

---

### `homescreen.widget.media`

Emitted on OCP player state changes (`gui.player.media.service.sync.status`)
and on track info responses (`ovos.common_play.track_info.response`).

```json
{
  "enabled": true,
  "state":   "playing",
  "widget":  { "title": "Song Name", "artist": "Artist", "album": "Album", "image": "..." }
}
```

`enabled` is `false` when the player is stopped.

---

## Bus events consumed

| Event | Action |
|---|---|
| `homescreen.register.examples` | Store examples per skill and language, then re-emit `.examples` |
| `homescreen.register.app` | Store app entry, then re-emit `.apps` |
| `detach_skill` | Remove skill from examples and apps, then re-emit affected events |
| `homescreen.wallpaper.set` | Parse and re-emit `.wallpaper` |
| `ovos.notification.update_counter` | Store count, request storage model, then re-emit `.notifications` |
| `ovos.notification.update_storage_model` | Store model, then re-emit `.notifications` |
| `skill-ovos-weather.openvoiceos.weather.response` | Parse report, then re-emit `.weather` |
| `mycroft.network.connected` | Set connectivity to `"network"`, then re-emit `.connectivity` |
| `mycroft.internet.connected` | Set connectivity to `"online"`, then re-emit `.connectivity` |
| `enclosure.notify.no_internet` | Set connectivity to `"offline"`, then re-emit `.connectivity` |
| `ovos.widgets.timer.*` | Re-emit `homescreen.widget.timer` |
| `ovos.widgets.alarm.*` | Re-emit `homescreen.widget.alarm` |
| `gui.player.media.service.sync.status` | Re-emit `homescreen.widget.media` with state |
| `ovos.common_play.track_info.response` | Re-emit `homescreen.widget.media` with track data |
| `mycroft.ready` | Re-push all cached state (apps, connectivity, wallpaper, notifications) |

---
[Home](../README.md)
