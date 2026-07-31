# ovos-legacy-mycroft-gui-plugin

This plugin is a GUI adapter for OpenVoiceOS. It translates the OVOS template API (the `SYSTEM_*` page templates) into the mycroft-gui Qt WebSocket protocol. Existing Qt5 and Qt6 GUI clients, such as [mycroft-gui-qt6](https://github.com/OpenVoiceOS/mycroft-gui-qt6), can then show skill output without any QML from the skill itself. Skills do not ship QML files. This plugin renders every page from its own bundled `ui/*.qml` files.

The plugin also runs `HomescreenManager`, which sends `homescreen.data.*` and `homescreen.widget.*` bus events. These events let a Qt shell, such as [ovos-shell](https://github.com/OpenVoiceOS/ovos-shell), show a live idle screen without depending on any skill. See [docs/homescreen.md](docs/homescreen.md) for the full list of events.

## Install

```bash
pip install ovos-legacy-mycroft-gui-plugin
```

The plugin registers under the `opm.gui_adapter` entry point:

```toml
[project.entry-points."opm.gui_adapter"]
ovos-legacy-mycroft-gui = "ovos_legacy_mycroft_gui:LegacyMycoftGuiPlugin"
```

[ovos-plugin-manager](https://github.com/OpenVoiceOS/ovos-plugin-manager) finds and loads it through this entry point. You do not call the plugin directly.

## Usage

Set the plugin as the GUI adapter in `mycroft.conf`:

```json
{
  "gui": {
    "module": "ovos-legacy-mycroft-gui"
  }
}
```

[ovos-gui](https://github.com/OpenVoiceOS/ovos-gui), the GUI daemon in ovos-core, loads the adapter named in this setting at startup. From that point, every `SYSTEM_*` template event a skill sends goes through this plugin and reaches connected Qt clients over its WebSocket server (default port 18181).

## Related projects

- [OpenVoiceOS/ovos-gui](https://github.com/OpenVoiceOS/ovos-gui): the GUI daemon that loads this adapter
- [OpenVoiceOS/ovos-plugin-manager](https://github.com/OpenVoiceOS/ovos-plugin-manager): defines the `AbstractGUIPlugin` base class and the `opm.gui_adapter` entry point
- [OpenVoiceOS/mycroft-gui-qt6](https://github.com/OpenVoiceOS/mycroft-gui-qt6): a Qt6 client that speaks the WebSocket protocol this plugin implements
- [OpenVoiceOS/ovos-shell](https://github.com/OpenVoiceOS/ovos-shell): a Qt shell that consumes the homescreen events this plugin emits

## License

Apache-2.0. See the `license` field in [pyproject.toml](pyproject.toml).
