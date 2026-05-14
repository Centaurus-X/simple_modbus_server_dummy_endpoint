import asyncio
import contextlib
import json
import unittest
from pathlib import Path

import aiohttp

import modbus_sim_server as server


class RuntimeHistorySyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_web_host = server.CONFIG["webserver"]["host"]
        self.original_web_port = server.CONFIG["webserver"]["port"]
        self.original_broadcast_ms = server.CONFIG["simulation"]["broadcast_interval_ms"]
        self.original_update_ms = server.CONFIG["simulation"]["update_interval_ms"]
        server.reset_sensor_data_tmp_storage()

    async def asyncTearDown(self):
        server.CONFIG["webserver"]["host"] = self.original_web_host
        server.CONFIG["webserver"]["port"] = self.original_web_port
        server.CONFIG["simulation"]["broadcast_interval_ms"] = self.original_broadcast_ms
        server.CONFIG["simulation"]["update_interval_ms"] = self.original_update_ms

    async def test_initial_websocket_state_contains_recorded_history(self):
        state = server.create_server_state()
        sensor = server.register_sensor(state, 10, "input_register")
        device_id = sensor["id"]

        server.update_sensor_value(state, device_id, 101, timestamp_ms=1_700_000_000_000)
        server.update_sensor_value(state, device_id, 202, timestamp_ms=1_700_000_004_000)
        server.flush_runtime_sensor_snapshot(state, force=True)

        snapshot_payload = json.loads(server.SENSOR_DATA_SNAPSHOT_FILE.read_text(encoding="utf-8"))
        self.assertIn(device_id, snapshot_payload["history"])
        self.assertEqual(len(snapshot_payload["history"][device_id]), 2)
        self.assertEqual(snapshot_payload["history"][device_id][0]["timestamp_ms"], 1_700_000_000_000)
        self.assertEqual(snapshot_payload["history"][device_id][1]["value"], 202)

        journal_lines = [
            json.loads(line)
            for line in server.SENSOR_DATA_EVENTS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(journal_lines), 2)
        self.assertEqual(journal_lines[0]["device_id"], device_id)
        self.assertEqual(journal_lines[1]["value"], 202)

        runner = await self._start_webserver(state)
        try:
            initial_state = await self._connect_and_get_initial_state(runner)
            self.assertEqual(initial_state["type"], "initial_state")
            self.assertIn("history", initial_state)
            self.assertIn(device_id, initial_state["history"])
            self.assertEqual(initial_state["history"][device_id][0]["timestamp_ms"], 1_700_000_000_000)
            self.assertEqual(initial_state["history"][device_id][1]["value"], 202)
        finally:
            await runner.cleanup()

    async def test_batch_broadcast_contains_real_samples(self):
        state = server.create_server_state()
        runner = await self._start_webserver(state)
        broadcast_task = None
        session = None
        ws = None

        try:
            server.CONFIG["simulation"]["broadcast_interval_ms"] = 50
            session = aiohttp.ClientSession()
            ws = await session.ws_connect(self._build_ws_url(runner))
            await ws.receive_json(timeout=2)

            broadcast_task = asyncio.create_task(server.broadcast_loop_task(state))

            sensor = server.register_sensor(state, 11, "input_register")
            device_id = sensor["id"]
            server.update_sensor_value(state, device_id, 11, timestamp_ms=1_700_000_010_000)
            server.update_sensor_value(state, device_id, 22, timestamp_ms=1_700_000_014_000)

            batch_message = await self._receive_message_of_type(ws, "batch_value_update")
            self.assertEqual(batch_message["device_type"], "sensor")
            self.assertEqual(batch_message["values"][device_id], 22)
            self.assertIn(device_id, batch_message["history"])
            self.assertEqual(len(batch_message["history"][device_id]), 2)
            self.assertEqual(batch_message["history"][device_id][0]["timestamp_ms"], 1_700_000_010_000)
            self.assertEqual(batch_message["history"][device_id][1]["timestamp_ms"], 1_700_000_014_000)
            self.assertIn(device_id, batch_message["devices"])
            self.assertEqual(batch_message["devices"][device_id]["read_count"], 2)
            self.assertEqual(batch_message["devices"][device_id]["value"], 22)
            self.assertIsNotNone(batch_message["devices"][device_id]["last_read"])
        finally:
            if broadcast_task is not None:
                broadcast_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await broadcast_task
            if ws is not None:
                await ws.close()
            if session is not None:
                await session.close()
            await runner.cleanup()

    async def test_initial_state_contains_current_config_and_meta(self):
        state = server.create_server_state()
        runner = await self._start_webserver(state)

        try:
            server.CONFIG["simulation"]["update_interval_ms"] = 4000
            server.CONFIG["simulation"]["broadcast_interval_ms"] = 5000
            initial_state = await self._connect_and_get_initial_state(runner)
            self.assertEqual(initial_state["config"]["simulation"]["update_interval_ms"], 4000)
            self.assertEqual(initial_state["config"]["simulation"]["broadcast_interval_ms"], 5000)
            self.assertEqual(initial_state["server_meta"]["app_version"], server.APP_VERSION)
            self.assertEqual(
                initial_state["server_meta"]["pymodbus_version"],
                server.pymodbus.__version__,
            )
        finally:
            await runner.cleanup()


    async def test_time_based_simulation_updates_do_not_increment_read_count(self):
        state = server.create_server_state()
        sensor = server.register_sensor(state, 12, "input_register")
        device_id = sensor["id"]

        server.set_simulation_config(state, device_id, {
            "mode": "sine",
            "amplitude": 1,
            "offset": 1,
            "frequency": 0.1,
        })
        server.CONFIG["simulation"]["update_interval_ms"] = 10

        simulation_task = asyncio.create_task(server.simulation_update_task(state))
        try:
            await asyncio.sleep(0.05)
        finally:
            simulation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await simulation_task

        self.assertEqual(state["sensors"][device_id]["read_count"], 0)
        self.assertEqual(state["stats"]["total_reads"], 0)
        self.assertIsNone(state["sensors"][device_id]["last_read"])
        self.assertIsNotNone(state["sensors"][device_id]["last_update"])

    async def test_time_based_modbus_read_counts_without_duplicate_history_sample(self):
        state = server.create_server_state()
        sensor = server.register_sensor(state, 13, "input_register")
        device_id = sensor["id"]
        server.set_simulation_config(state, device_id, {
            "mode": "sine",
            "amplitude": 1,
            "offset": 1,
            "frequency": 0.1,
        })
        server.update_sensor_value(
            state,
            device_id,
            123,
            count_as_read=False,
            timestamp_ms=1_700_000_020_000,
        )
        history_before = len(state["history"][device_id])

        data_block_class = server.create_simulation_datablock(state, 500, "input_register")
        data_block = data_block_class(0, [0] * 32)
        values = data_block.getValues(13, 1)

        self.assertEqual(values, [123])
        self.assertEqual(state["sensors"][device_id]["read_count"], 1)
        self.assertEqual(state["stats"]["total_reads"], 1)
        self.assertEqual(len(state["history"][device_id]), history_before)

    async def test_set_sim_interval_accepts_string_values_and_broadcasts_normalized_config(self):
        state = server.create_server_state()
        runner = await self._start_webserver(state)

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self._build_ws_url(runner)) as ws:
                await ws.receive_json(timeout=2)
                await ws.send_json({
                    "type": "set_sim_interval",
                    "update_interval_ms": "4100",
                    "broadcast_interval_ms": "5200",
                })
                message = await self._receive_message_of_type(ws, "config_changed")
                self.assertEqual(message["config"]["simulation"]["update_interval_ms"], 4100)
                self.assertEqual(message["config"]["simulation"]["broadcast_interval_ms"], 5200)
                self.assertEqual(message["server_meta"]["app_version"], server.APP_VERSION)
                self.assertEqual(server.CONFIG["simulation"]["update_interval_ms"], 4100)
                self.assertEqual(server.CONFIG["simulation"]["broadcast_interval_ms"], 5200)

        await runner.cleanup()

    async def _start_webserver(self, state):
        server.CONFIG["webserver"]["host"] = "127.0.0.1"
        server.CONFIG["webserver"]["port"] = 0
        return await server.run_webserver(state)

    def _build_ws_url(self, runner):
        site = next(iter(runner.sites))
        socket = site._server.sockets[0]
        host, port = socket.getsockname()[:2]
        return f"ws://{host}:{port}/ws"

    async def _connect_and_get_initial_state(self, runner):
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self._build_ws_url(runner)) as ws:
                return await ws.receive_json(timeout=2)

    async def _receive_message_of_type(self, ws, message_type):
        while True:
            message = await ws.receive_json(timeout=2)
            if message.get("type") == message_type:
                return message


if __name__ == "__main__":
    unittest.main(verbosity=2)
