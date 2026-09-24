"""Test plugin instantiation."""
from ovos_legacy_mycroft_gui import LegacyMycoftGuiPlugin


def test_plugin_instantiation_and_config():
    """Test plugin instantiation and configuration."""
    config = {"gui_ws": {"host": "localhost", "port": 18181}}
    plugin = LegacyMycoftGuiPlugin(config)
    
    assert plugin is not None
    assert isinstance(plugin, LegacyMycoftGuiPlugin)
    assert hasattr(plugin, '_namespaces')
    assert hasattr(plugin, '_active_stack')
