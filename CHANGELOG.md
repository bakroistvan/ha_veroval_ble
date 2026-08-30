# Changelog

All notable changes to this project are documented here.

HACS shows the **GitHub Release tag** as the installed version. After editing this file and `custom_components/veroval_ble/manifest.json`, tag that same version (for example `0.1.0`) and push the tag so [`.github/workflows/release.yml`](.github/workflows/release.yml) can publish the release.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-08-29

### Added

- Home Assistant custom integration for the Veroval compact+ BPU 26 (`BPU26`) over BLE.
- Config flow with host-adapter pairing (6-digit PIN) and per-slot devices (User 1 / User 2).
- Sensors: systolic, diastolic, pulse, measured time, user slot; irregular-pulse binary sensor.
- HIL dump script and unit tests for parser, client, coordinator, and config flow.

[0.1.0]: https://github.com/bakroistvan/ha_veroval_ble/releases/tag/0.1.0
