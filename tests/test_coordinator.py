"""Tests for update coordinator."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.tauron_amiplus.connector import TauronAmiplusRawData
from custom_components.tauron_amiplus.coordinator import TauronAmiplusUpdateCoordinator


async def test_coordinator_update_method_with_statistics(mocker):
    mock_hass = MagicMock()
    mock_connector_cls = mocker.patch("custom_components.tauron_amiplus.coordinator.TauronAmiplusConnector")
    mock_connector = mock_connector_cls.return_value

    mock_data = TauronAmiplusRawData()
    mock_connector.get_raw_data = AsyncMock(return_value=mock_data)
    mock_connector.async_save_cache = AsyncMock()

    mock_updater_cls = mocker.patch("custom_components.tauron_amiplus.coordinator.TauronAmiplusStatisticsUpdater")
    mock_updater = mock_updater_cls.return_value
    mock_updater.update_all = AsyncMock()

    coordinator = TauronAmiplusUpdateCoordinator(
        mock_hass,
        "entry123",
        "user",
        "pass",
        "12345",
        "Dom",
        store_statistics=True,
        config_entry=MagicMock(),
    )

    data = await coordinator.update_method()
    assert data is mock_data

    mock_connector.get_raw_data.assert_awaited_once()
    mock_updater.update_all.assert_awaited_once_with(mock_data)
    mock_connector.async_save_cache.assert_awaited_once()


async def test_coordinator_update_method_without_statistics(mocker):
    mock_hass = MagicMock()
    mock_connector_cls = mocker.patch("custom_components.tauron_amiplus.coordinator.TauronAmiplusConnector")
    mock_connector = mock_connector_cls.return_value

    mock_data = TauronAmiplusRawData()
    mock_connector.get_raw_data = AsyncMock(return_value=mock_data)
    mock_connector.async_save_cache = AsyncMock()

    mock_updater_cls = mocker.patch("custom_components.tauron_amiplus.coordinator.TauronAmiplusStatisticsUpdater")

    coordinator = TauronAmiplusUpdateCoordinator(
        mock_hass,
        "entry123",
        "user",
        "pass",
        "12345",
        "Dom",
        store_statistics=False,
        config_entry=MagicMock(),
    )

    data = await coordinator.update_method()
    assert data is mock_data

    mock_connector.get_raw_data.assert_awaited_once()
    mock_updater_cls.assert_not_called()
    mock_connector.async_save_cache.assert_awaited_once()
