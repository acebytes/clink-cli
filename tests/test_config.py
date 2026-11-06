"""
Tests for configuration
"""

from config import (
    DEFAULT_MODEL,
    TEMPERATURE_ANALYTICAL,
    TEMPERATURE_BALANCED,
    TEMPERATURE_CREATIVE,
    __author__,
    __updated__,
    __version__,
)


class TestConfig:
    """Test configuration values"""

    def test_version_info(self):
        """Test version information exists and has correct format"""
        # Check version format (e.g., "2.4.1")
        assert isinstance(__version__, str)
        assert len(__version__.split(".")) == 3  # Major.Minor.Patch

        # Check author
        assert "Fahad Gilani" in __author__
        assert "Beehive Innovations" in __author__

        # Check updated date exists (don't assert on specific format/value)
        assert isinstance(__updated__, str)

    def test_model_config(self):
        """Test model configuration"""
        # Native CLI defaults replace the hosted-provider model default.
        assert DEFAULT_MODEL == "auto"

    def test_temperature_defaults(self):
        """Test temperature constants"""
        assert TEMPERATURE_ANALYTICAL == 1.0
        assert TEMPERATURE_BALANCED == 1.0
        assert TEMPERATURE_CREATIVE == 1.0
