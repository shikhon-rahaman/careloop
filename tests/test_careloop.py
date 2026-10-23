"""
CareLoop Test Suite (tests/test_careloop.py)

Tests the 3 core features:
1. MCP tool invocation via Streamable HTTP with automatic fallback to tools.py
2. Medication late vs on-time adherence logic in log_medication
3. Demo mode (DEMO_MODE=1) relative schedule seeding (-1h, +2h, +5h) and demo reset
"""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn

import db
import tools
from intents import format_reply, match_intent
from server import app


class TestCareLoop(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_intent_parsing(self):
        """Test intents.py extracts proper tools and arguments."""
        # 1. log_wellness mood='dizzy'
        i1 = match_intent("I feel dizzy")
        self.assertEqual(i1["tool"], "log_wellness")
        self.assertEqual(i1["arguments"], {"mood": "dizzy"})

        # 2. log_medication name='BP tablet'
        i2 = match_intent("I took my BP tablet")
        self.assertEqual(i2["tool"], "log_medication")
        self.assertEqual(i2["arguments"], {"name": "BP tablet"})

        # 3. check_missed_meds
        i3 = match_intent("Did I miss anything?")
        self.assertEqual(i3["tool"], "check_missed_meds")
        self.assertEqual(i3["arguments"], {})

        # 4. get_daily_summary
        i4 = match_intent("How was my day?")
        self.assertEqual(i4["tool"], "get_daily_summary")
        self.assertEqual(i4["arguments"], {})

    def test_log_medication_adherence(self):
        """Test log_medication marks late when >60m and only praises 'on time' when truly on time."""
        os.environ["DEMO_MODE"] = "0"
        db.init_db(db_path=self.db_path)

        # Baseline: BP tablet scheduled at 08:00
        # A: On time (08:20 vs 08:00) -> on time
        res_ontime = tools.log_medication(name="BP tablet", taken_at="2026-10-23 08:20:00", db_path=self.db_path)
        self.assertTrue(res_ontime["is_on_time"])
        self.assertFalse(res_ontime["is_late"])
        self.assertIn("on time", res_ontime["message"])
        reply_ontime = format_reply("log_medication", {"name": "BP tablet"}, res_ontime)
        self.assertIn("on time", reply_ontime)

        # B: Late (09:30 vs 08:00, 90 mins late) -> noted as late
        res_late = tools.log_medication(name="BP tablet", taken_at="2026-10-23 09:30:00", db_path=self.db_path)
        self.assertTrue(res_late["is_late"])
        self.assertFalse(res_late["is_on_time"])
        expected_msg = "I've logged your BP tablet. It was due at 08:00, so I've noted it as late."
        self.assertEqual(res_late["message"], expected_msg)
        reply_late = format_reply("log_medication", {"name": "BP tablet"}, res_late)
        self.assertEqual(reply_late, expected_msg)

        # C: Early (05:00 vs 08:00, 180 mins early) -> must NOT say 'on time'
        res_early = tools.log_medication(name="BP tablet", taken_at="2026-10-23 05:00:00", db_path=self.db_path)
        self.assertTrue(res_early["is_early"])
        self.assertFalse(res_early["is_on_time"])
        self.assertNotIn("on time", res_early["message"])
        reply_early = format_reply("log_medication", {"name": "BP tablet"}, res_early)
        self.assertNotIn("on time", reply_early)

        # D: Unscheduled medicine -> must NOT say 'on time'
        res_unsched = tools.log_medication(name="Aspirin", taken_at="2026-10-23 08:00:00", db_path=self.db_path)
        self.assertFalse(res_unsched["is_on_time"])
        self.assertNotIn("on time", res_unsched["message"])
        reply_unsched = format_reply("log_medication", {"name": "Aspirin"}, res_unsched)
        self.assertNotIn("on time", reply_unsched)

    def test_demo_mode_schedule_and_reset(self):
        """Test DEMO_MODE=1 seeds relative schedule (-1h, +2h, +5h) and starts with 0 missed."""
        os.environ["DEMO_MODE"] = "1"
        self.assertTrue(db.is_demo_mode())

        db.init_db(db_path=self.db_path)
        sched = db.get_schedule(db_path=self.db_path)
        self.assertEqual(len(sched), 3)

        now = datetime.now()
        expected_minus1 = (now - timedelta(hours=1)).strftime("%H:%M")
        expected_plus2 = (now + timedelta(hours=2)).strftime("%H:%M")
        expected_plus5 = (now + timedelta(hours=5)).strftime("%H:%M")

        sched_map = {item["name"]: item["time_hhmm"] for item in sched}
        self.assertEqual(sched_map["BP tablet"], expected_minus1)
        self.assertEqual(sched_map["Metformin"], expected_plus2)
        self.assertEqual(sched_map["Vitamin D"], expected_plus5)

        # Verify 0 missed medicines at startup
        summary = tools.get_daily_summary(db_path=self.db_path)
        self.assertEqual(len(summary["missed"]), 0)

        # Reset demo restores clean schedule and state
        tools.log_medication("BP tablet", db_path=self.db_path)
        self.assertEqual(len(db.get_med_logs(db_path=self.db_path)), 1)
        db.reset_db(db_path=self.db_path)
        self.assertEqual(len(db.get_med_logs(db_path=self.db_path)), 0)

        summary_reset = tools.get_daily_summary(db_path=self.db_path)
        self.assertEqual(len(summary_reset["missed"]), 0)


class TestAsyncServerE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ["DEMO_MODE"] = "1"
        os.environ["PORT"] = "8098"
        self.config = uvicorn.Config(app, host="127.0.0.1", port=8098, log_level="error")
        self.server = uvicorn.Server(self.config)
        self.server_task = asyncio.create_task(self.server.serve())
        await asyncio.sleep(1.0)
        self.base_url = "http://127.0.0.1:8098"

    async def asyncTearDown(self):
        self.server.should_exit = True
        await self.server_task

    async def test_chat_mcp_call_and_fallback(self):
        """Test chat endpoint makes MCP call and falls back gracefully if connection fails."""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            # 1. Normal MCP invocation
            resp = await client.post("/api/chat", json={"message": "I feel dizzy"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["tool"], "log_wellness")
            self.assertEqual(data["mcp_call"], "MCP call: log_wellness(mood='dizzy')")
            self.assertTrue(data["data"]["alert_triggered"])

            # 2. Test fallback to tools.py directly when MCP client fails
            with patch("server._invoke_mcp_client", side_effect=ConnectionError("Simulated MCP error")):
                resp_fb = await client.post("/api/chat", json={"message": "I feel dizzy"})
                self.assertEqual(resp_fb.status_code, 200)
                data_fb = resp_fb.json()
                self.assertEqual(data_fb["tool"], "log_wellness")
                self.assertEqual(data_fb["mcp_call"], "MCP call: log_wellness(mood='dizzy')")
                self.assertEqual(data_fb["data"]["status"], "success")
                self.assertTrue(data_fb["data"]["alert_triggered"])


if __name__ == "__main__":
    unittest.main()
