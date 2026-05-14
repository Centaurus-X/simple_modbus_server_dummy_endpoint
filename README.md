# Advanced Modbus Simulation Server

Advanced Modbus Simulation Server is a Python-based Modbus TCP simulator with a browser-based dashboard, WebSocket live synchronization and configurable sensor simulation modes.

The server is designed for engineering, automation testing, PLC integration experiments and UI development where a repeatable Modbus endpoint is needed without physical hardware.

## Features

- Modbus TCP server with dynamic sensor and actuator registration.
- Web dashboard with live values, charts, event log, device reset actions and runtime configuration.
- WebSocket synchronization for initial state, registry updates, batched value updates, history and server statistics.
- Simulation modes: random, constant, PID, ramp, sine, noise, error and manual.
- Runtime sensor history persisted as snapshot and JSONL event stream.
- Test suite for runtime history, WebSocket initialization, batch broadcasts and timing configuration.

## Compatibility

This release supports Python 3.9+ and `pymodbus>=3.5,<3.13`.

`pymodbus 3.13+` changed the legacy datastore API used by this project. The dependency is pinned below 3.13 until the server is migrated to the newer simulator data model.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python modbus_sim_server.py
```

Open the dashboard:

```text
http://127.0.0.1:8080
```

Default ports:

| Service | Host | Port |
| --- | --- | ---: |
| Modbus TCP | `0.0.0.0` | `5020` |
| Web dashboard | `0.0.0.0` | `8080` |

## Address model

The default sensor/actuator threshold is `500`.

| Address range | Device type |
| --- | --- |
| `< 500` | Sensor |
| `>= 500` | Actuator |
| Discrete inputs and input registers | Read-only sensors |

Devices are registered dynamically when Modbus clients read from or write to an address.

## Runtime files

The server writes runtime data under `logs/`:

```text
logs/
├─ system_<timestamp>.log
└─ sensor_data_tmp/
   ├─ sensor_history_snapshot.json
   └─ sensor_history_events.jsonl
```

These files are intentionally ignored by Git. The repository keeps only `.gitkeep` placeholders.

## WebSocket endpoint

```text
ws://<host>:<web_port>/ws
```

The first message sent to a client is `initial_state`. Afterwards the server sends registry updates, batch value updates, history data and statistics. See [`docs/components.md`](docs/components.md) for message details.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall .
node --check web_interface/app.js
```

The release ZIP was checked with the commands above.

## Project structure

```text
.
├─ modbus_sim_server.py
├─ web_interface/
├─ tests/
├─ config/
├─ docs/
├─ logs/
├─ requirements.txt
├─ pyproject.toml
├─ README.md
├─ CONTRIBUTING.md
├─ SECURITY.md
└─ LICENSE
```

## License

Licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE).
