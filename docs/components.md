# Components and protocol

## Web interface

Path: `web_interface/`

- `index.html`: dashboard layout.
- `app.js`: WebSocket client, state synchronization, charts and UI actions.
- `style.css`: dashboard styling.

## WebSocket endpoint

```text
ws://<host>:<web_port>/ws
```

## Server to client messages

### `initial_state`

Complete initial server state. Important fields:

- `sensors`
- `actuators`
- `history`
- `config`
- `simulation_modes`
- `theme`
- `server_meta`
- `runtime_recording`

### `registry_update`

Device registry changes. Typical events:

- `sensor_added`
- `actuator_added`
- `device_reset`

### `batch_value_update`

Periodic delta update for changed values. Important fields:

- `device_type`
- `values`
- `devices`: current device snapshot including counters, timestamps and mode.
- `history`: samples recorded since the previous broadcast.
- `timestamp_ms`

### `config_update`

Simulation configuration for a single device changed.

### `config_changed`

Server-wide configuration changed, most commonly simulation or broadcast timing.

### `history_data`

Explicitly requested history for one device.

### `server_stats`

Server statistics for dashboard counters and uptime.

### `theme_changed`

Theme change confirmation sent by the server.

## Client to server messages

### `set_simulation`

Set the simulation configuration for one device.

### `bulk_set_simulation`

Set one simulation configuration for multiple sensors.

### `set_value`

Set a manual value directly and synchronize it immediately.

### `set_sim_interval`

Set server-wide intervals:

- `update_interval_ms`
- `broadcast_interval_ms`

### `get_history`

Request the full history for one device.

### `get_stats`

Request current server statistics.

### `reset_device`

Reset a device including history and runtime state.

### `set_theme`

Change the UI theme.
