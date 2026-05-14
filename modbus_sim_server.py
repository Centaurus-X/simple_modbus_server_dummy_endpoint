#!/usr/bin/env python3
"""
===============================================================================
ADVANCED MODBUS SIMULATION SERVER v2.3
===============================================================================
Compatible with pymodbus 3.5 through 3.12.x.
===============================================================================

Release notes in v2.3:
- Timing defaults and runtime configuration are synchronized consistently.
- Server metadata and version information are provided by the server.
- Batch updates include device metadata such as counters, timestamps and mode.
- Runtime history stays server-side authoritative and time-based.
- The Web UI displays ports, thresholds, version and intervals consistently.
===============================================================================
"""

import asyncio
import json
import logging
import math
import os
import re
import shutil
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from threading import Lock
from random import randint, uniform, choice
from typing import Any, Dict, Optional, Set

# ============================================================================
# PYMODBUS IMPORTS - automatic version detection
# ============================================================================

import pymodbus


def parse_version_triplet(version_text):
    """Parse a dotted version string into a comparable numeric triplet."""
    parts = []
    for raw_part in str(version_text).split('.'):
        match = re.match(r"(\d+)", raw_part)
        if match is None:
            parts.append(0)
        else:
            parts.append(int(match.group(1)))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


PYMODBUS_VERSION = parse_version_triplet(getattr(pymodbus, "__version__", "0.0.0"))

if PYMODBUS_VERSION >= (3, 13, 0):
    raise RuntimeError(
        "pymodbus 3.13+ changed the legacy datastore API used by this "
        "server. Install pymodbus>=3.5,<3.13."
    )

from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock

# Server import
try:
    from pymodbus.server import StartAsyncTcpServer
except ImportError:
    from pymodbus.server.async_io import StartAsyncTcpServer

# Check whether ModbusSlaveContext is available (pymodbus < 3.7)
USE_LEGACY_SLAVE_CONTEXT = False
ModbusSlaveContext = None

try:
    from pymodbus.datastore import ModbusSlaveContext
    USE_LEGACY_SLAVE_CONTEXT = True
except ImportError:
    try:
        from pymodbus.datastore.context import ModbusSlaveContext
        USE_LEGACY_SLAVE_CONTEXT = True
    except ImportError:
        try:
            from pymodbus.datastore.store import ModbusSlaveContext
            USE_LEGACY_SLAVE_CONTEXT = True
        except ImportError:
            USE_LEGACY_SLAVE_CONTEXT = False

# Device Identification
try:
    from pymodbus.device import ModbusDeviceIdentification
except ImportError:
    try:
        from pymodbus.constants import DeviceInformation as ModbusDeviceIdentification
    except ImportError:
        ModbusDeviceIdentification = None

# WebSocket and HTTP
from aiohttp import web, WSMsgType

if hasattr(web, "AppKey"):
    APP_STATE_KEY = web.AppKey("state", dict)
else:
    APP_STATE_KEY = "state"

APP_VERSION = "2.3"
APP_PRODUCT_NAME = "Advanced Modbus Simulation Server"

# ============================================================================
# PATH CONFIGURATION
# ============================================================================

BASE_DIR = Path(__file__).parent.resolve()
WEB_INTERFACE_DIR = BASE_DIR / "web_interface"
CONFIG_DIR = BASE_DIR / "config"
LOGS_DIR = BASE_DIR / "logs"
SENSOR_DATA_TMP_DIR = LOGS_DIR / "sensor_data_tmp"
SENSOR_DATA_SNAPSHOT_FILE = SENSOR_DATA_TMP_DIR / "sensor_history_snapshot.json"
SENSOR_DATA_EVENTS_FILE = SENSOR_DATA_TMP_DIR / "sensor_history_events.jsonl"
RUNTIME_FILE_LOCK = Lock()

LOGS_DIR.mkdir(exist_ok=True)

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    "modbus": {
        "host": "0.0.0.0",
        "port": 5020,
        "sensor_threshold": 500,
        "datablock_size": 2000,
    },
    "webserver": {
        "host": "0.0.0.0",
        "port": 8080,
    },
    "simulation": {
        "default_mode": "random",
        "update_interval_ms": 4000,
        "broadcast_interval_ms": 5000,
        "history_length": 500,
    },
    "pid": {
        "default_kp": 1.0,
        "default_ki": 0.1,
        "default_kd": 0.05,
        "default_setpoint": 50.0,
    },
    "logging": {
        "level": "INFO",
        "file_enabled": True,
    }
}


def reset_sensor_data_tmp_storage():
    """Reset the temporary runtime directory for sensor history files."""
    SENSOR_DATA_TMP_DIR.mkdir(exist_ok=True)
    for entry in SENSOR_DATA_TMP_DIR.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except FileNotFoundError:
                pass

    empty_snapshot = {
        "format": "sensor_history_snapshot",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history_length": CONFIG["simulation"]["history_length"],
        "sensor_count": 0,
        "history": {},
    }
    SENSOR_DATA_SNAPSHOT_FILE.write_text(
        json.dumps(empty_snapshot, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    SENSOR_DATA_EVENTS_FILE.touch()


reset_sensor_data_tmp_storage()

# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging():
    """Configure the logging system."""
    log_format = '%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s'
    date_format = '%H:%M:%S'

    handlers = [logging.StreamHandler(sys.stdout)]

    if CONFIG["logging"]["file_enabled"]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = LOGS_DIR / f"system_{timestamp}.log"
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    logging.basicConfig(
        format=log_format,
        level=getattr(logging, CONFIG["logging"]["level"]),
        datefmt=date_format,
        handlers=handlers
    )

    logging.getLogger('pymodbus').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)

    return logging.getLogger("ModbusSimServer")

logger = setup_logging()

# ============================================================================
# SIMULATION MODES
# ============================================================================

SIMULATION_MODES = {
    "random": "Random values",
    "constant": "Constant value",
    "pid": "PID simulation",
    "ramp": "Ramp simulation",
    "sine": "Sine wave",
    "noise": "Noise",
    "error": "Error simulation",
    "manual": "Manual value",
}

# Modes that need the periodic time-based loop
TIME_BASED_MODES = frozenset({"sine", "ramp", "pid", "noise"})

# Modes that update only on Modbus read/write events
EVENT_BASED_MODES = frozenset({"random", "constant", "manual", "error"})

# ============================================================================
# GLOBAL SERVER STATE - SINGLE SOURCE OF TRUTH
# ============================================================================

def create_server_state():
    """Create the global server state."""
    return {
        "sensors": {},
        "actuators": {},
        "websockets": set(),
        "start_time": datetime.now(timezone.utc),
        "stats": {
            "total_reads": 0,
            "total_writes": 0,
            "errors": 0,
        },
        "history": {},
        "pending_history_samples": {"sensor": {}, "actuator": {}},
        "simulation_configs": {},
        "pid_states": {},
        "ramp_states": {},
        "server_running": True,
        "theme": "dark",
        "runtime_history_dirty": False,
        # Pending broadcast queue instead of per-value asyncio.create_task calls
        "pending_broadcasts": deque(maxlen=5000),
        # Dirty flags for changed values (batch broadcast)
        "dirty_sensors": set(),
        "dirty_actuators": set(),
        # Lock placeholder for thread-safety if pymodbus calls from another thread
        "_value_lock": None,  # wird spaeter als asyncio.Lock gesetzt
    }

SERVER_STATE = create_server_state()

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_timestamp():
    return datetime.now(timezone.utc).isoformat()


def clamp(value, min_val, max_val):
    return max(min_val, min(max_val, value))


def safe_json_dumps(obj):
    def default_handler(o):
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, set):
            return list(o)
        if isinstance(o, deque):
            return list(o)
        return str(o)
    return json.dumps(obj, default=default_handler, ensure_ascii=False)


def clone_json_data(obj):
    return json.loads(safe_json_dumps(obj))


def parse_int_value(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_interval_value(value, *, min_value=50, max_value=60000):
    parsed = parse_int_value(value)
    if parsed is None:
        return None
    if parsed < min_value or parsed > max_value:
        return None
    return parsed


def build_public_config():
    config_copy = clone_json_data(CONFIG)

    simulation = config_copy.setdefault("simulation", {})
    simulation["update_interval_ms"] = parse_int_value(
        simulation.get("update_interval_ms"),
        4000,
    )
    simulation["broadcast_interval_ms"] = parse_int_value(
        simulation.get("broadcast_interval_ms"),
        5000,
    )
    simulation["history_length"] = parse_int_value(
        simulation.get("history_length"),
        500,
    )
    simulation.setdefault("default_mode", "random")

    modbus = config_copy.setdefault("modbus", {})
    modbus["port"] = parse_int_value(modbus.get("port"), 5020)
    modbus["sensor_threshold"] = parse_int_value(
        modbus.get("sensor_threshold"),
        500,
    )
    modbus["datablock_size"] = parse_int_value(
        modbus.get("datablock_size"),
        2000,
    )
    modbus.setdefault("host", "0.0.0.0")

    webserver = config_copy.setdefault("webserver", {})
    webserver["port"] = parse_int_value(webserver.get("port"), 8080)
    webserver.setdefault("host", "0.0.0.0")

    pid_config = config_copy.setdefault("pid", {})
    pid_config.setdefault("default_kp", 1.0)
    pid_config.setdefault("default_ki", 0.1)
    pid_config.setdefault("default_kd", 0.05)
    pid_config.setdefault("default_setpoint", 50.0)

    logging_config = config_copy.setdefault("logging", {})
    logging_config.setdefault("level", "INFO")
    logging_config.setdefault("file_enabled", True)

    return config_copy


def build_server_meta(state=None):
    meta = {
        "app_version": APP_VERSION,
        "product_name": APP_PRODUCT_NAME,
        "pymodbus_version": pymodbus.__version__,
        "web_interface_dir": str(WEB_INTERFACE_DIR),
        "runtime_recording": build_runtime_recording_info(),
    }

    if state is not None:
        meta["server_start_time"] = state["start_time"].isoformat()
        meta["theme"] = state.get("theme", "dark")

    return meta


def get_timestamp_ms():
    return int(time.time() * 1000)


def timestamp_ms_to_iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000.0, timezone.utc).isoformat()


def get_history_maxlen():
    return int(CONFIG["simulation"].get("history_length", 500))


def ensure_history_buffer(state, device_id):
    history = state["history"].get(device_id)
    maxlen = get_history_maxlen()

    if history is None or history.maxlen != maxlen:
        history = deque(list(history or []), maxlen=maxlen)
        state["history"][device_id] = history

    return history


def build_history_entry(value, timestamp_ms=None):
    effective_timestamp_ms = int(
        timestamp_ms if timestamp_ms is not None else get_timestamp_ms()
    )
    return {
        "timestamp_ms": effective_timestamp_ms,
        "value": value,
    }


def queue_history_sample_for_broadcast(state, device_type, device_id, sample):
    pending = state["pending_history_samples"].setdefault(device_type, {})
    if device_id not in pending:
        pending[device_id] = []
    pending[device_id].append(sample)


def pop_pending_history_samples(state, device_type, device_ids):
    pending = state["pending_history_samples"].setdefault(device_type, {})
    samples = {}

    for device_id in device_ids:
        device_samples = pending.pop(device_id, None)
        if device_samples:
            samples[device_id] = device_samples

    return samples


def build_runtime_sensor_event(device_id, sample):
    timestamp_ms = sample["timestamp_ms"]
    return {
        "event": "sample",
        "device_id": device_id,
        "timestamp_ms": timestamp_ms,
        "timestamp_iso": timestamp_ms_to_iso(timestamp_ms),
        "value": sample["value"],
    }


def append_runtime_sensor_event(device_id, sample):
    event = build_runtime_sensor_event(device_id, sample)
    line = json.dumps(event, ensure_ascii=False) + "\n"

    with RUNTIME_FILE_LOCK:
        with SENSOR_DATA_EVENTS_FILE.open('a', encoding='utf-8') as fp:
            fp.write(line)


def build_history_payload(state, device_ids=None):
    payload = {}

    if device_ids is None:
        device_ids = list(state["history"].keys())

    for device_id in device_ids:
        history = state["history"].get(device_id)
        if history is None:
            payload[device_id] = []
        else:
            payload[device_id] = list(history)

    return payload


def build_runtime_sensor_snapshot(state):
    sensor_history = {}

    for device_id in state["sensors"]:
        sensor_history[device_id] = list(state["history"].get(device_id, []))

    return {
        "format": "sensor_history_snapshot",
        "generated_at": get_timestamp(),
        "generated_at_ms": get_timestamp_ms(),
        "server_start_time": state["start_time"].isoformat(),
        "history_length": get_history_maxlen(),
        "sensor_count": len(state["sensors"]),
        "history": sensor_history,
    }


def atomic_write_json(filepath, payload):
    temp_filepath = filepath.with_suffix(filepath.suffix + '.tmp')
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)

    with RUNTIME_FILE_LOCK:
        temp_filepath.write_text(serialized, encoding='utf-8')
        os.replace(temp_filepath, filepath)


def flush_runtime_sensor_snapshot(state, force=False):
    if not force and not state.get("runtime_history_dirty"):
        return

    snapshot = build_runtime_sensor_snapshot(state)
    atomic_write_json(SENSOR_DATA_SNAPSHOT_FILE, snapshot)
    state["runtime_history_dirty"] = False


def record_history_sample(
    state,
    device_id,
    device_type,
    value,
    timestamp_ms=None,
    persist_sensor_sample=True,
):
    sample = build_history_entry(value, timestamp_ms)
    history = ensure_history_buffer(state, device_id)
    history.append(sample)
    queue_history_sample_for_broadcast(state, device_type, device_id, sample)

    if device_type == "sensor":
        state["runtime_history_dirty"] = True
        if persist_sensor_sample:
            append_runtime_sensor_event(device_id, sample)

    return sample


def clear_device_history_buffers(state, device_id):
    if device_id in state["history"]:
        state["history"][device_id].clear()

    for device_type in ("sensor", "actuator"):
        pending = state["pending_history_samples"].setdefault(device_type, {})
        pending.pop(device_id, None)

    state["dirty_sensors"].discard(device_id)
    state["dirty_actuators"].discard(device_id)

    if device_id in state["sensors"]:
        state["runtime_history_dirty"] = True


def build_runtime_recording_info():
    return {
        "tmp_dir": str(SENSOR_DATA_TMP_DIR.relative_to(BASE_DIR)),
        "snapshot_file": str(SENSOR_DATA_SNAPSHOT_FILE.relative_to(BASE_DIR)),
        "events_file": str(SENSOR_DATA_EVENTS_FILE.relative_to(BASE_DIR)),
    }


def build_device_snapshot_map(state, device_type, device_ids):
    if device_type == "sensor":
        source = state["sensors"]
    else:
        source = state["actuators"]

    snapshot = {}
    for device_id in device_ids:
        if device_id in source:
            snapshot[device_id] = clone_json_data(source[device_id])

    return snapshot


def build_initial_state_payload(state):
    return {
        "type": "initial_state",
        "sensors": clone_json_data(state["sensors"]),
        "actuators": clone_json_data(state["actuators"]),
        "history": build_history_payload(state),
        "config": build_public_config(),
        "simulation_modes": SIMULATION_MODES,
        "theme": state["theme"],
        "runtime_recording": build_runtime_recording_info(),
        "server_meta": build_server_meta(state),
        "timestamp": get_timestamp(),
        "timestamp_ms": get_timestamp_ms(),
    }

# ============================================================================
# SIMULATION ENGINE
# ============================================================================

def calculate_random_value(config):
    min_val = config.get("min", 0)
    max_val = config.get("max", 65535)
    is_float = config.get("float", False)
    if is_float:
        return uniform(min_val, max_val)
    return randint(int(min_val), int(max_val))


def calculate_constant_value(config):
    return config.get("value", 0)


def calculate_pid_value(device_id, config, state):
    pid_state = state.get("pid_states", {}).get(device_id, {})
    setpoint = config.get("setpoint", CONFIG["pid"]["default_setpoint"])
    kp = config.get("kp", CONFIG["pid"]["default_kp"])
    ki = config.get("ki", CONFIG["pid"]["default_ki"])
    kd = config.get("kd", CONFIG["pid"]["default_kd"])

    current = pid_state.get("current", setpoint * 0.5)
    integral = pid_state.get("integral", 0)
    last_error = pid_state.get("last_error", 0)

    noise = uniform(-2, 2)
    disturbance = config.get("disturbance", 0)
    error = setpoint - current
    integral = clamp(integral + error * 0.01, -100, 100)
    derivative = (error - last_error) / 0.01
    output = kp * error + ki * integral + kd * derivative
    current = current + output * 0.1 + noise + disturbance
    current = clamp(current, 0, 65535)

    if "pid_states" not in state:
        state["pid_states"] = {}
    state["pid_states"][device_id] = {
        "current": current,
        "integral": integral,
        "last_error": error,
    }
    return int(current)


def calculate_ramp_value(config, state, device_id):
    start_val = config.get("start", 0)
    end_val = config.get("end", 65535)
    duration_ms = config.get("duration_ms", 10000)
    loop = config.get("loop", True)

    if "ramp_states" not in state:
        state["ramp_states"] = {}
    ramp_state = state.get("ramp_states", {}).get(device_id, {})
    start_time = ramp_state.get("start_time", time.time())
    if device_id not in state["ramp_states"]:
        state["ramp_states"][device_id] = {"start_time": start_time}

    elapsed = (time.time() - start_time) * 1000
    if loop:
        elapsed = elapsed % duration_ms
    else:
        elapsed = min(elapsed, duration_ms)
    progress = elapsed / duration_ms
    value = start_val + (end_val - start_val) * progress
    return int(clamp(value, 0, 65535))


def calculate_sine_value(config):
    amplitude = config.get("amplitude", 32767)
    offset = config.get("offset", 32767)
    frequency = config.get("frequency", 0.1)
    phase = config.get("phase", 0)
    t = time.time()
    value = offset + amplitude * math.sin(2 * math.pi * frequency * t + phase)
    return int(clamp(value, 0, 65535))


def calculate_noise_value(config):
    base_value = config.get("base", 32767)
    noise_level = config.get("noise_level", 1000)
    noise = uniform(-noise_level, noise_level)
    return int(clamp(base_value + noise, 0, 65535))


def calculate_error_value(config):
    error_type = config.get("error_type", "stuck")
    if error_type == "stuck":
        return config.get("stuck_value", 0)
    elif error_type == "overflow":
        return 65535
    elif error_type == "underflow":
        return 0
    elif error_type == "spike":
        if uniform(0, 1) < config.get("spike_probability", 0.1):
            return choice([0, 65535])
        return config.get("normal_value", 32767)
    elif error_type == "dropout":
        if uniform(0, 1) < config.get("dropout_probability", 0.1):
            return 0
        return config.get("normal_value", 32767)
    return 0


def calculate_simulation_value(device_id, device_type, config, state):
    mode = config.get("mode", "random")
    calculators = {
        "random": partial(calculate_random_value, config),
        "constant": partial(calculate_constant_value, config),
        "pid": partial(calculate_pid_value, device_id, config, state),
        "ramp": partial(calculate_ramp_value, config, state, device_id),
        "sine": partial(calculate_sine_value, config),
        "noise": partial(calculate_noise_value, config),
        "error": partial(calculate_error_value, config),
        "manual": partial(calculate_constant_value, config),
    }
    calculator = calculators.get(mode, partial(calculate_random_value, config))
    return calculator()

# ============================================================================
# DEVICE REGISTRY - synchronous only, no asyncio.create_task calls
# ============================================================================

def register_sensor(state, address, function_type, initial_value=None):
    """Register a sensor. Synchronous only; no broadcast is sent here."""
    sensor_id = f"sensor_{function_type}_{address}"
    if sensor_id not in state["sensors"]:
        state["sensors"][sensor_id] = {
            "id": sensor_id,
            "address": address,
            "function_type": function_type,
            "value": initial_value if initial_value is not None else 0,
            "last_read": None,
            "last_update": None,
            "read_count": 0,
            "registered_at": get_timestamp(),
            "simulation_mode": "random",
            "config": {"min": 0, "max": 65535},
        }
        logger.info(f"Sensor registered: {sensor_id} @ address {address}")
        # Broadcast via Queue statt asyncio.create_task
        state["pending_broadcasts"].append({
            "type": "registry_update",
            "event": "sensor_added",
            "data": state["sensors"][sensor_id],
            "timestamp": get_timestamp(),
        })
    return state["sensors"][sensor_id]


def register_actuator(state, address, function_type, initial_value=None):
    """Register an actuator. Synchronous only; no broadcast is sent here."""
    actuator_id = f"actuator_{function_type}_{address}"
    if actuator_id not in state["actuators"]:
        state["actuators"][actuator_id] = {
            "id": actuator_id,
            "address": address,
            "function_type": function_type,
            "value": initial_value if initial_value is not None else 0,
            "last_write": None,
            "last_update": None,
            "write_count": 0,
            "registered_at": get_timestamp(),
        }
        logger.info(f"Actuator registered: {actuator_id} @ address {address}")
        state["pending_broadcasts"].append({
            "type": "registry_update",
            "event": "actuator_added",
            "data": state["actuators"][actuator_id],
            "timestamp": get_timestamp(),
        })
    return state["actuators"][actuator_id]


def mark_sensor_read(state, sensor_id):
    if sensor_id in state["sensors"]:
        sensor = state["sensors"][sensor_id]
        sensor["last_read"] = get_timestamp()
        sensor["read_count"] += 1
        state["stats"]["total_reads"] += 1
        state["dirty_sensors"].add(sensor_id)


def update_sensor_value(state, sensor_id, value, count_as_read=True, timestamp_ms=None):
    """Update a sensor value in state and record its history."""
    if sensor_id in state["sensors"]:
        sensor = state["sensors"][sensor_id]
        sensor["value"] = value
        sensor["last_update"] = get_timestamp()

        if count_as_read:
            mark_sensor_read(state, sensor_id)

        record_history_sample(
            state,
            sensor_id,
            "sensor",
            value,
            timestamp_ms=timestamp_ms,
        )
        # Mark as dirty for batch broadcasting
        state["dirty_sensors"].add(sensor_id)


def update_actuator_value(state, actuator_id, value, count_as_write=True, timestamp_ms=None):
    """Update an actuator value in state and synchronize its history."""
    if actuator_id in state["actuators"]:
        actuator = state["actuators"][actuator_id]
        actuator["value"] = value
        actuator["last_update"] = get_timestamp()

        if count_as_write:
            actuator["last_write"] = actuator["last_update"]
            actuator["write_count"] += 1
            state["stats"]["total_writes"] += 1

        record_history_sample(
            state,
            actuator_id,
            "actuator",
            value,
            timestamp_ms=timestamp_ms,
            persist_sensor_sample=False,
        )
        # Mark as dirty for batch broadcasting
        state["dirty_actuators"].add(actuator_id)


def set_simulation_config(state, device_id, config):
    """Set simulation configuration for a sensor or actuator."""
    state["simulation_configs"][device_id] = config
    mode = config.get("mode", "random")

    # Handle both sensors and actuators
    if device_id in state["sensors"]:
        state["sensors"][device_id]["simulation_mode"] = mode
        state["sensors"][device_id]["config"] = config
    if device_id in state["actuators"]:
        # Actuators can receive a simulation configuration as well
        state["actuators"][device_id]["simulation_mode"] = mode
        state["actuators"][device_id]["config"] = config

    # Reset ramp state on mode changes
    if mode == "ramp" and device_id in state.get("ramp_states", {}):
        del state["ramp_states"][device_id]
    # Reset PID state on mode changes
    if mode != "pid" and device_id in state.get("pid_states", {}):
        del state["pid_states"][device_id]

    logger.info(f"Simulation config: {device_id} -> {mode}")
    state["pending_broadcasts"].append({
        "type": "config_update",
        "device_id": device_id,
        "config": config,
        "timestamp": get_timestamp(),
        "timestamp_ms": get_timestamp_ms(),
    })

# ============================================================================
# WEBSOCKET BROADCASTS
# ============================================================================

async def broadcast_to_websockets(state, message):
    """Send a message to all connected WebSockets."""
    if not state["websockets"]:
        return
    message_str = safe_json_dumps(message)
    dead_sockets = set()
    for ws in state["websockets"].copy():
        try:
            await ws.send_str(message_str)
        except Exception:
            dead_sockets.add(ws)
    state["websockets"] -= dead_sockets


async def broadcast_server_stats(state):
    """Send server statistics to all WebSockets."""
    uptime = (datetime.now(timezone.utc) - state["start_time"]).total_seconds()
    await broadcast_to_websockets(state, {
        "type": "server_stats",
        "uptime_seconds": uptime,
        "total_sensors": len(state["sensors"]),
        "total_actuators": len(state["actuators"]),
        "total_reads": state["stats"]["total_reads"],
        "total_writes": state["stats"]["total_writes"],
        "errors": state["stats"]["errors"],
        "connected_websockets": len(state["websockets"]),
        "timestamp": get_timestamp(),
    })

# ============================================================================
# PERIODIC TASKS - system heartbeat
# ============================================================================

async def simulation_update_task(state):
    """
    Periodic simulation loop for time-based modes only.
    Modes such as sine, ramp, pid and noise are updated here periodically.
    Event-based modes (random, constant, manual, error) are
    calculated only on Modbus reads inside the data block.
    """
    while state["server_running"]:
        try:
            interval = CONFIG["simulation"]["update_interval_ms"] / 1000.0

            for sensor_id, sensor in list(state["sensors"].items()):
                config = state["simulation_configs"].get(
                    sensor_id,
                    sensor.get("config", {"mode": "random", "min": 0, "max": 65535})
                )
                mode = config.get("mode", "random")

                # Update only time-based modes in this loop
                if mode not in TIME_BASED_MODES:
                    continue

                is_bit_type = sensor.get("function_type") in ("coil", "discrete")

                new_value = calculate_simulation_value(
                    sensor_id, "sensor", config, state
                )

                if is_bit_type:
                    new_value = 1 if new_value > 0.5 else 0
                else:
                    new_value = int(clamp(new_value, 0, 65535))

                # Time-based simulation changes the value but is not a real Modbus read
                update_sensor_value(
                    state,
                    sensor_id,
                    new_value,
                    count_as_read=False,
                )

            await asyncio.sleep(interval)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in simulation loop: {e}")
            await asyncio.sleep(1)


async def broadcast_loop_task(state):
    """
    Batch broadcast instead of per-value asyncio.create_task calls.
    Collects dirty flags and pending broadcasts and sends them periodically.
    The interval is read from CONFIG on each tick and can be changed at runtime.
    """
    while state["server_running"]:
        try:
            interval = CONFIG["simulation"]["broadcast_interval_ms"] / 1000.0
            # 1) Send pending broadcasts (registry updates, config updates)
            while state["pending_broadcasts"]:
                msg = state["pending_broadcasts"].popleft()
                await broadcast_to_websockets(state, msg)

            # 2) Batch broadcast dirty sensor values
            dirty_sensors = state["dirty_sensors"].copy()
            state["dirty_sensors"].clear()

            if dirty_sensors:
                # Send one batch update instead of individual messages
                batch = {}
                for sensor_id in dirty_sensors:
                    sensor = state["sensors"].get(sensor_id)
                    if sensor:
                        batch[sensor_id] = sensor["value"]

                if batch:
                    await broadcast_to_websockets(state, {
                        "type": "batch_value_update",
                        "device_type": "sensor",
                        "values": batch,
                        "devices": build_device_snapshot_map(
                            state,
                            "sensor",
                            dirty_sensors,
                        ),
                        "history": pop_pending_history_samples(
                            state, "sensor", dirty_sensors
                        ),
                        "timestamp": get_timestamp(),
                        "timestamp_ms": get_timestamp_ms(),
                    })

            # 3) Batch broadcast dirty actuator values
            dirty_actuators = state["dirty_actuators"].copy()
            state["dirty_actuators"].clear()

            if dirty_actuators:
                batch = {}
                for actuator_id in dirty_actuators:
                    actuator = state["actuators"].get(actuator_id)
                    if actuator:
                        batch[actuator_id] = actuator["value"]

                if batch:
                    await broadcast_to_websockets(state, {
                        "type": "batch_value_update",
                        "device_type": "actuator",
                        "values": batch,
                        "devices": build_device_snapshot_map(
                            state,
                            "actuator",
                            dirty_actuators,
                        ),
                        "history": pop_pending_history_samples(
                            state, "actuator", dirty_actuators
                        ),
                        "timestamp": get_timestamp(),
                        "timestamp_ms": get_timestamp_ms(),
                    })

            await asyncio.sleep(interval)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in broadcast loop: {e}")
            await asyncio.sleep(1)


async def runtime_snapshot_task(state):
    """Persist the current sensor history periodically as a snapshot."""
    while state["server_running"]:
        try:
            flush_runtime_sensor_snapshot(state)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error while persisting sensor history: {e}")
            await asyncio.sleep(1)


async def stats_broadcast_task(state):
    """Periodic statistics broadcast."""
    while state["server_running"]:
        try:
            await broadcast_server_stats(state)
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in statistics broadcast: {e}")
            await asyncio.sleep(5)

# ============================================================================
# CUSTOM DATA BLOCK - state dictionary as the single source of truth
# ============================================================================

def create_simulation_datablock(state, threshold, data_type):
    """
    Create a data block that uses the state dictionary as the authoritative source.
    The internal ModbusSequentialDataBlock storage is used only as fallback storage.
    """

    class SimulationDataBlock(ModbusSequentialDataBlock):
        """Data block synchronized with the global state dictionary."""

        def __init__(self, address, values):
            super().__init__(address, values)
            self._data_type = data_type
            self._threshold = threshold
            self._state = state

        def getValues(self, address, count=1):
            """
            Read values from the state dictionary (single source of truth).

            Time-based modes (sine, ramp, pid, noise):
                The value comes from state and is kept current by simulation_update_task.

            Event-based modes (random, constant, manual, error):
                The value is recalculated here on Modbus read.
                A new value is produced only for an actual client read.
            """
            values = []
            is_bit_type = self._data_type in ('coil', 'discrete')
            is_read_only = self._data_type in ('discrete', 'input_register')

            for offset in range(count):
                abs_addr = address + offset

                is_sensor = (abs_addr < self._threshold) or is_read_only

                if is_sensor:
                    sensor_id = f"sensor_{self._data_type}_{abs_addr}"

                    # Register if needed (idempotent and synchronous)
                    register_sensor(self._state, abs_addr, self._data_type)

                    sensor = self._state["sensors"].get(sensor_id, {})
                    config = self._state["simulation_configs"].get(
                        sensor_id,
                        sensor.get(
                            "config",
                            {"mode": "random", "min": 0,
                             "max": 1 if is_bit_type else 65535}
                        )
                    )
                    mode = config.get("mode", "random")

                    if mode in EVENT_BASED_MODES:
                        # Event-based mode: recalculate on every read
                        val = calculate_simulation_value(
                            sensor_id, "sensor", config, self._state
                        )
                        if is_bit_type:
                            val = 1 if val > 0.5 else 0
                        else:
                            val = int(clamp(val, 0, 65535))

                        # Update state
                        update_sensor_value(self._state, sensor_id, val)
                    else:
                        # Time-based mode: read value from state
                        # Updated by simulation_update_task
                        val = sensor.get("value", 0)
                        mark_sensor_read(self._state, sensor_id)
                        if is_bit_type:
                            val = 1 if val > 0 else 0

                    # Synchronize internal storage with the state dictionary
                    try:
                        super().setValues(abs_addr, [val])
                    except Exception:
                        pass

                    values.append(val)
                else:
                    # Actuator
                    actuator_id = f"actuator_{self._data_type}_{abs_addr}"
                    register_actuator(self._state, abs_addr, self._data_type)

                    # Read from the state dictionary as the primary source
                    actuator = self._state["actuators"].get(actuator_id, {})
                    val = actuator.get("value", 0)

                    # Fall back to internal storage only when state is empty
                    if val == 0 and actuator_id in self._state["actuators"]:
                        try:
                            internal_val = super().getValues(abs_addr, 1)[0]
                            if internal_val != 0:
                                val = internal_val
                                self._state["actuators"][actuator_id]["value"] = val
                        except Exception:
                            pass

                    values.append(val)

            return values

        def setValues(self, address, values):
            """
            Write values into both the state dictionary and internal storage.
            Beide bleiben synchron.
            """
            is_read_only = self._data_type in ('discrete', 'input_register')

            if is_read_only:
                return  # Read-only

            for i, val in enumerate(values):
                abs_addr = address + i

                if abs_addr >= self._threshold:
                    # Actuator
                    actuator_id = f"actuator_{self._data_type}_{abs_addr}"
                    register_actuator(self._state, abs_addr, self._data_type)
                    update_actuator_value(self._state, actuator_id, val)
                else:
                    # Sensor: switch to manual mode
                    sensor_id = f"sensor_{self._data_type}_{abs_addr}"
                    register_sensor(self._state, abs_addr, self._data_type)
                    # Set manual mode so the simulation loop
                    # does not overwrite this value
                    set_simulation_config(
                        self._state, sensor_id,
                        {"mode": "manual", "value": val}
                    )
                    # Set value directly in the state dictionary
                    if sensor_id in self._state["sensors"]:
                        update_sensor_value(
                            self._state,
                            sensor_id,
                            val,
                            count_as_read=False,
                        )

                # Synchronize internal storage
                try:
                    super().setValues(abs_addr, [val])
                except Exception:
                    pass

    return SimulationDataBlock

# ============================================================================
# MODBUS SERVER SETUP
# ============================================================================

def setup_modbus_server(state):
    """Configure the Modbus server for supported pymodbus versions."""
    threshold = CONFIG["modbus"]["sensor_threshold"]
    size = CONFIG["modbus"]["datablock_size"]

    # Create data block classes
    CoilDataBlock = create_simulation_datablock(state, threshold, "coil")
    DiscreteDataBlock = create_simulation_datablock(state, threshold, "discrete")
    HoldingRegisterDataBlock = create_simulation_datablock(
        state, threshold, "register"
    )
    InputRegisterDataBlock = create_simulation_datablock(
        state, threshold, "input_register"
    )

    # Create data blocks
    di_block = DiscreteDataBlock(0, [0] * size)
    co_block = CoilDataBlock(0, [0] * size)
    hr_block = HoldingRegisterDataBlock(0, [0] * size)
    ir_block = InputRegisterDataBlock(0, [0] * size)

    # Create context based on pymodbus version
    if USE_LEGACY_SLAVE_CONTEXT and ModbusSlaveContext is not None:
        logger.info("Using legacy ModbusSlaveContext")
        store = ModbusSlaveContext(
            di=di_block,
            co=co_block,
            hr=hr_block,
            ir=ir_block,
            zero_mode=True,
        )
        context = ModbusServerContext(slaves=store, single=True)
    else:
        logger.info("Using newer pymodbus API (3.7+)")

        class SimpleSlaveContext:
            """Small compatibility replacement for ModbusSlaveContext in pymodbus 3.7+."""
            def __init__(self, di, co, hr, ir):
                self.store = {
                    'd': di,  # Discrete Inputs
                    'c': co,  # Coils
                    'h': hr,  # Holding Registers
                    'i': ir,  # Input Registers
                }
                self.zero_mode = True

            def getValues(self, fc_as_hex, address, count=1):
                fx_mapper = {
                    1: 'c', 2: 'd', 3: 'h', 4: 'i',
                    5: 'c', 6: 'h', 15: 'c', 16: 'h',
                }
                fx = fx_mapper.get(fc_as_hex, 'h')
                return self.store[fx].getValues(address, count)

            def setValues(self, fc_as_hex, address, values):
                fx_mapper = {5: 'c', 6: 'h', 15: 'c', 16: 'h'}
                fx = fx_mapper.get(fc_as_hex, 'h')
                return self.store[fx].setValues(address, values)

            def validate(self, fc_as_hex, address, count=1):
                return True

        store = SimpleSlaveContext(
            di=di_block,
            co=co_block,
            hr=hr_block,
            ir=ir_block,
        )
        context = ModbusServerContext(slaves=store, single=True)

    # Device Identification (optional)
    identity = None
    if ModbusDeviceIdentification is not None:
        try:
            identity = ModbusDeviceIdentification()
            identity.VendorName = "AdvancedModbusSimServer"
            identity.ProductCode = "AMSS-2.3"
            identity.ProductName = APP_PRODUCT_NAME
            identity.ModelName = f"Modbus Sim Server v{APP_VERSION}"
            identity.MajorMinorRevision = f"{APP_VERSION}.0"
        except Exception:
            identity = None

    logger.info(
        f"Modbus server configured: threshold={threshold}, size={size}"
    )

    return context, identity

# ============================================================================
# WEB SERVER AND WEBSOCKET HANDLERS
# ============================================================================

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    state = request.app[APP_STATE_KEY]
    state["websockets"].add(ws)

    client_ip = request.remote
    logger.info(f"WebSocket connected: {client_ip}")

    await ws.send_str(safe_json_dumps(build_initial_state_payload(state)))

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    await handle_websocket_message(state, ws, data)
                except json.JSONDecodeError:
                    pass
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        state["websockets"].discard(ws)
        logger.info(f"WebSocket disconnected: {client_ip}")

    return ws


async def handle_websocket_message(state, ws, data):
    msg_type = data.get("type")

    if msg_type == "set_simulation":
        device_id = data.get("device_id")
        config = data.get("config", {})
        if device_id:
            set_simulation_config(state, device_id, config)

    elif msg_type == "set_value":
        device_id = data.get("device_id")
        value = data.get("value")
        if device_id and value is not None:
            set_simulation_config(
                state, device_id, {"mode": "manual", "value": value}
            )
            # Direkt den Wert setzen
            if device_id in state["sensors"]:
                update_sensor_value(
                    state, device_id, value, count_as_read=False
                )
            if device_id in state["actuators"]:
                update_actuator_value(
                    state, device_id, value, count_as_write=False
                )

    elif msg_type == "set_theme":
        theme = data.get("theme", "dark")
        state["theme"] = theme
        await broadcast_to_websockets(state, {
            "type": "theme_changed", "theme": theme,
            "timestamp": get_timestamp()
        })

    elif msg_type == "get_history":
        device_id = data.get("device_id")
        if device_id:
            await ws.send_str(safe_json_dumps({
                "type": "history_data",
                "device_id": device_id,
                "data": build_history_payload(state, [device_id]).get(device_id, []),
                "timestamp": get_timestamp(),
                "timestamp_ms": get_timestamp_ms(),
            }))

    elif msg_type == "get_stats":
        await broadcast_server_stats(state)

    elif msg_type == "reset_device":
        device_id = data.get("device_id")
        if device_id:
            if device_id in state["sensors"]:
                state["sensors"][device_id]["value"] = 0
                state["sensors"][device_id]["read_count"] = 0
                state["sensors"][device_id]["simulation_mode"] = "random"
                state["sensors"][device_id]["config"] = {
                    "min": 0, "max": 65535
                }
            if device_id in state["actuators"]:
                state["actuators"][device_id]["value"] = 0
                state["actuators"][device_id]["write_count"] = 0
            clear_device_history_buffers(state, device_id)
            if device_id in state["simulation_configs"]:
                del state["simulation_configs"][device_id]
            if device_id in state.get("pid_states", {}):
                del state["pid_states"][device_id]
            if device_id in state.get("ramp_states", {}):
                del state["ramp_states"][device_id]
            reset_device_type = "sensor" if device_id in state["sensors"] else "actuator"
            reset_device_snapshot = None
            if reset_device_type == "sensor" and device_id in state["sensors"]:
                reset_device_snapshot = clone_json_data(state["sensors"][device_id])
            if reset_device_type == "actuator" and device_id in state["actuators"]:
                reset_device_snapshot = clone_json_data(state["actuators"][device_id])
            state["pending_broadcasts"].append({
                "type": "registry_update",
                "event": "device_reset",
                "data": {
                    "device_id": device_id,
                    "device_type": reset_device_type,
                    "device": reset_device_snapshot,
                },
                "timestamp": get_timestamp(),
                "timestamp_ms": get_timestamp_ms(),
            })

    elif msg_type == "bulk_set_simulation":
        devices = data.get("devices", [])
        config = data.get("config", {})
        for device_id in devices:
            set_simulation_config(state, device_id, config)

    elif msg_type == "set_sim_interval":
        # Runtime-configurable intervals
        update_ms = normalize_interval_value(data.get("update_interval_ms"))
        broadcast_ms = normalize_interval_value(data.get("broadcast_interval_ms"))

        if update_ms is not None:
            CONFIG["simulation"]["update_interval_ms"] = update_ms
            logger.info(
                f"Simulation interval changed: {update_ms}ms"
            )
        if broadcast_ms is not None:
            CONFIG["simulation"]["broadcast_interval_ms"] = broadcast_ms
            logger.info(
                f"Broadcast interval changed: {broadcast_ms}ms"
            )
        await broadcast_to_websockets(state, {
            "type": "config_changed",
            "config": build_public_config(),
            "server_meta": build_server_meta(state),
            "timestamp": get_timestamp(),
            "timestamp_ms": get_timestamp_ms(),
        })

# ============================================================================
# HTTP ROUTES
# ============================================================================

async def index_handler(request):
    html_path = WEB_INTERFACE_DIR / "index.html"
    if html_path.exists():
        return web.FileResponse(html_path)
    return web.Response(text="Web interface not found", status=404)


async def api_status_handler(request):
    state = request.app[APP_STATE_KEY]
    uptime = (
        datetime.now(timezone.utc) - state["start_time"]
    ).total_seconds()
    return web.json_response({
        "status": "running",
        "uptime_seconds": uptime,
        "sensors_count": len(state["sensors"]),
        "actuators_count": len(state["actuators"]),
        "websockets_count": len(state["websockets"]),
        "stats": state["stats"],
        "config": build_public_config(),
        "server_meta": build_server_meta(state),
        "modbus": {
            "host": CONFIG["modbus"]["host"],
            "port": CONFIG["modbus"]["port"]
        },
        "timestamp": get_timestamp(),
    })


async def api_devices_handler(request):
    state = request.app[APP_STATE_KEY]
    return web.json_response({
        "sensors": state["sensors"],
        "actuators": state["actuators"],
        "timestamp": get_timestamp(),
    })


async def api_set_simulation_handler(request):
    state = request.app[APP_STATE_KEY]
    try:
        data = await request.json()
        device_id = data.get("device_id")
        config = data.get("config", {})
        if device_id:
            set_simulation_config(state, device_id, config)
            return web.json_response({"success": True})
        return web.json_response(
            {"success": False, "error": "device_id required"}, status=400
        )
    except Exception as e:
        return web.json_response(
            {"success": False, "error": str(e)}, status=500
        )


async def api_config_handler(request):
    return web.json_response({
        "config": build_public_config(),
        "server_meta": build_server_meta(),
        "simulation_modes": SIMULATION_MODES,
        "timestamp": get_timestamp(),
    })

# ============================================================================
# MAIN
# ============================================================================

async def run_webserver(state):
    app = web.Application()
    app[APP_STATE_KEY] = state

    app.router.add_get('/', index_handler)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/api/status', api_status_handler)
    app.router.add_get('/api/devices', api_devices_handler)
    app.router.add_get('/api/config', api_config_handler)
    app.router.add_post('/api/simulation', api_set_simulation_handler)

    if WEB_INTERFACE_DIR.exists():
        app.router.add_static('/static/', WEB_INTERFACE_DIR, name='static')
        app.router.add_get(
            '/app.js',
            partial(
                _serve_static_file,
                filepath=WEB_INTERFACE_DIR / "app.js"
            )
        )
        app.router.add_get(
            '/style.css',
            partial(
                _serve_static_file,
                filepath=WEB_INTERFACE_DIR / "style.css"
            )
        )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(
        runner, CONFIG["webserver"]["host"], CONFIG["webserver"]["port"]
    )
    await site.start()

    actual_port = CONFIG["webserver"]["port"]
    if site._server is not None and site._server.sockets:
        actual_port = site._server.sockets[0].getsockname()[1]

    logger.info(
        f"Webserver: http://{CONFIG['webserver']['host']}:"
        f"{actual_port}"
    )
    return runner


async def _serve_static_file(request, filepath):
    """Statische Datei ausliefern - functools.partial kompatibel."""
    return web.FileResponse(filepath)


async def run_modbus_server(state):
    context, identity = setup_modbus_server(state)
    address = (CONFIG["modbus"]["host"], CONFIG["modbus"]["port"])
    logger.info(f"Starting Modbus TCP server on {address}...")

    kwargs = {"context": context, "address": address}
    if identity is not None:
        kwargs["identity"] = identity

    await StartAsyncTcpServer(**kwargs)


async def main():
    state = SERVER_STATE

    logger.info("=" * 70)
    logger.info(f"ADVANCED MODBUS SIMULATION SERVER v{APP_VERSION}")
    logger.info("=" * 70)
    logger.info(f"Modbus port:         {CONFIG['modbus']['port']}")
    logger.info(f"Web port:            {CONFIG['webserver']['port']}")
    logger.info(f"Sensor/actuator threshold: {CONFIG['modbus']['sensor_threshold']}")
    logger.info(f"Simulation interval:       {CONFIG['simulation']['update_interval_ms']}ms")
    logger.info(f"Broadcast interval: {CONFIG['simulation']['broadcast_interval_ms']}ms")
    logger.info(f"Web interface:       {WEB_INTERFACE_DIR}")
    logger.info(f"pymodbus version:    {pymodbus.__version__}")
    logger.info("=" * 70)

    try:
        await run_webserver(state)

        # Start periodic tasks
        asyncio.create_task(simulation_update_task(state))
        asyncio.create_task(broadcast_loop_task(state))
        asyncio.create_task(runtime_snapshot_task(state))
        asyncio.create_task(stats_broadcast_task(state))

        await run_modbus_server(state)
    except KeyboardInterrupt:
        logger.info("Server is shutting down...")
    except Exception as e:
        logger.error(f"Critical error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        state["server_running"] = False
        flush_runtime_sensor_snapshot(state, force=True)


def run():
    """Run the server from a console entry point."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    run()
