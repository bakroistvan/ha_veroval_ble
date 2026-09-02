# Changelog

All notable changes to this project are documented here.

HACS shows the **GitHub Release tag** as the installed version. After editing this file and `custom_components/veroval_ble/manifest.json`, tag that same version (for example `0.1.0`) and push the tag so [`.github/workflows/release.yml`](.github/workflows/release.yml) can publish the release.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.0] - 2026-09-02

### Added

- **Force data sync** button on the BPU26 device page (same GATT dump as `veroval_ble.force_dump`).

### Changed

- One Home Assistant device and one config entry per cuff. User 1 and User 2 readings are named entities on that device (`Systolic (User 1)`, …).
- One GATT dump publishes both memory slots. Setup no longer asks which user to add.
- `force_dump` targets the cuff, not a user slot.

### Breaking

- Upgrading from 0.2.0 migrates `{mac}_1` / `{mac}_2` config entries into one `{mac}` entry. Area assignments on leftover slot devices may need a one-time cleanup.
- Connected and RSSI unique ids are now `{mac}_connected` / `{mac}_rssi` (no `_1` / `_2` suffix).
- The User slot diagnostic sensor is removed (the user is in the entity name).

## [0.2.0] - 2026-08-31

### Added

- HACS Action validates with zero ignores (description, topics, license); brand icon tracked for store validation.
- 20-second phone-first grace so medi.connect can take the cuff before Home Assistant connects. Unavailable during the wait skips that window.
- New advertise window after idle unavailable or 20s advertisement silence (no 180s mute after setup). Fixes subsequent-measurement skip.
- Developer action `veroval_ble.force_dump` to sync immediately, including during grace.
- Last-synchronized diagnostic timestamp sensor per user slot.
- Connected diagnostic binary sensor: on while the host adapter has a GATT link to the cuff (`bluetoothctl` Connected: yes), including during a dump.

### Fixed

- Cached Bluetooth advertisements no longer keep the advertise window open after the cuff sleeps, so a later measurement can start a new dump. Stale discoveries no longer show an Add card.
- `force_dump` uses a device selector (hassfest no longer allows a device filter on `target`).
- A 20s phone-grace timer starts the dump even when Home Assistant sends no further advertisements.
- Cloud Agent `environment.json`, HACS packaging (LICENSE, brand icon, CI, issue templates).

## [0.1.0] - 2026-08-29

### Added

- Home Assistant custom integration for the Veroval compact+ BPU 26 (`BPU26`) over BLE.
- Config flow with host-adapter pairing (6-digit PIN) and per-slot devices (User 1 / User 2).
- Sensors: systolic, diastolic, pulse, measured time, user slot; irregular-pulse binary sensor.
- HIL dump script and unit tests for parser, client, coordinator, and config flow.

[0.3.0]: https://github.com/bakroistvan/ha_veroval_ble/releases/tag/0.3.0
[0.2.0]: https://github.com/bakroistvan/ha_veroval_ble/releases/tag/0.2.0
[0.1.0]: https://github.com/bakroistvan/ha_veroval_ble/releases/tag/0.1.0
