"""Test module imports."""
import ovos_legacy_mycroft_gui
from ovos_legacy_mycroft_gui import LegacyMycoftGuiPlugin


def test_import_module():
    """Test that the module imports without error."""
    assert ovos_legacy_mycroft_gui is not None


def test_import_plugin_class():
    """Test that the plugin class imports without error."""
    assert LegacyMycoftGuiPlugin is not None


def test_plugin_is_class():
    """Test that the plugin is a class."""
    assert isinstance(LegacyMycoftGuiPlugin, type)
