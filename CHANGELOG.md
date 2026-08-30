# Changelog

All notable changes to this project are documented here.

HACS shows the **GitHub Release tag** as the installed version. After editing this file and `custom_components/veroval_ble/manifest.json`, tag that same version (for example `0.1.0`) and push the tag so [`.github/workflows/release.yml`](.github/workflows/release.yml) can publish the release.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- 60-second phone-first grace so medi.connect can take the cuff before Home Assistant connects. Unavailable during the wait skips that window.
- New advertise window after idle unavailable or 20s advertisement silence (no 180s mute after setup). Fixes subsequent-measurement skip.
- Developer action `veroval_ble.force_dump` to sync immediately, including during grace.
- Last-synchronized diagnostic timestamp sensor per user slot.
- Advertising diagnostic binary sensor: on while a *fresh* BPU26 advertisement or host RSSI is recent, not while Home Assistant’s scanner cache keeps the device “available”.

### Fixed

- Cached Bluetooth advertisements no longer keep the advertise window open after the cuff sleeps, so a later measurement can start a new dump. Stale discoveries no longer show an Add card.
- `force_dump` uses a device selector (hassfest no longer allows a device filter on `target`).
- Advertising turns off a few seconds after the last live sighting (not a full 2-minute linger after the cuff sleeps). A 60s phone-grace timer starts the dump even when Home Assistant sends no further advertisements.
- Cloud Agent `environment.json`, HACS packaging (LICENSE, brand icon, CI, issue templates).

## [0.1.0] - 2026-08-29

### Added

- Home Assistant custom integration for the Veroval compact+ BPU 26 (`BPU26`) over BLE.
- Config flow with host-adapter pairing (6-digit PIN) and per-slot devices (User 1 / User 2).
- Sensors: systolic, diastolic, pulse, measured time, user slot; irregular-pulse binary sensor.
- HIL dump script and unit tests for parser, client, coordinator, and config flow.

[0.1.0]: https://github.com/bakroistvan/ha_veroval_ble/releases/tag/0.1.0
