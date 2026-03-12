"""Tests for SurveillanceStore and SurveillanceSkill."""

from __future__ import annotations

import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# SurveillanceStore tests
# ---------------------------------------------------------------------------


class TestSurveillanceStore:
    """Tests for the in-process observation store."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        """Patch data paths to use temp directory."""
        self.data_dir = str(tmp_path)
        self.obs_file = os.path.join(self.data_dir, "observations.jsonl")
        self.roster_file = os.path.join(self.data_dir, "roster.json")

        with (
            patch("hackathon.surveillance_store.DATA_DIR", self.data_dir),
            patch("hackathon.surveillance_store.OBS_FILE", self.obs_file),
            patch("hackathon.surveillance_store.ROSTER_FILE", self.roster_file),
        ):
            from hackathon.surveillance_store import SurveillanceStore

            self.store = SurveillanceStore()
            yield
            self.store.stop()

    def _sighting(self, pid="person-1", activity="working", lt_id=1, ts=None):
        return {
            "person_id": pid,
            "long_term_id": lt_id,
            "activity": activity,
            "first_seen": ts or time.time(),
            "last_seen": ts or time.time(),
        }

    def test_first_sighting_written(self):
        self.store.on_person_sighting(self._sighting())
        assert os.path.exists(self.obs_file)
        with open(self.obs_file) as f:
            lines = f.readlines()
        assert len(lines) == 1
        obs = json.loads(lines[0])
        assert obs["person_id"] == "person-1"
        assert obs["activity"] == "working"

    def test_roster_updated(self):
        self.store.on_person_sighting(self._sighting())
        assert os.path.exists(self.roster_file)
        with open(self.roster_file) as f:
            roster = json.load(f)
        assert "person-1" in roster
        assert roster["person-1"]["activity"] == "working"

    def test_same_activity_throttled(self):
        self.store.on_person_sighting(self._sighting(activity="sitting"))
        self.store.on_person_sighting(self._sighting(activity="sitting"))
        self.store.on_person_sighting(self._sighting(activity="sitting"))
        with open(self.obs_file) as f:
            lines = f.readlines()
        # Only first should be written (rest throttled — same activity, <5s apart)
        assert len(lines) == 1

    def test_activity_change_not_throttled(self):
        self.store.on_person_sighting(self._sighting(activity="sitting"))
        self.store.on_person_sighting(self._sighting(activity="standing"))
        self.store.on_person_sighting(self._sighting(activity="walking"))
        with open(self.obs_file) as f:
            lines = f.readlines()
        assert len(lines) == 3

    def test_multiple_people(self):
        self.store.on_person_sighting(self._sighting(pid="person-1", activity="sitting"))
        self.store.on_person_sighting(self._sighting(pid="person-2", activity="standing"))
        with open(self.obs_file) as f:
            lines = f.readlines()
        assert len(lines) == 2
        with open(self.roster_file) as f:
            roster = json.load(f)
        assert len(roster) == 2

    def test_roster_preserves_first_seen(self):
        self.store.on_person_sighting(self._sighting(activity="sitting"))
        with open(self.roster_file) as f:
            first_seen_1 = json.load(f)["person-1"]["first_seen"]
        self.store.on_person_sighting(self._sighting(activity="standing"))
        with open(self.roster_file) as f:
            first_seen_2 = json.load(f)["person-1"]["first_seen"]
        # first_seen should not change on subsequent sightings
        assert first_seen_1 == first_seen_2

    def test_stop_closes_file(self):
        self.store.on_person_sighting(self._sighting())
        self.store.stop()
        assert self.store._obs_file.closed


# ---------------------------------------------------------------------------
# SurveillanceSkill tests
# ---------------------------------------------------------------------------


class TestSurveillanceSkill:
    """Tests for the MCP-exposed skill module."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.data_dir = str(tmp_path)
        self.obs_file = os.path.join(self.data_dir, "observations.jsonl")
        self.roster_file = os.path.join(self.data_dir, "roster.json")

        with (
            patch("hackathon.surveillance_skill._DATA_DIR", self.data_dir),
            patch("hackathon.surveillance_skill._OBS_FILE", self.obs_file),
            patch("hackathon.surveillance_skill._ROSTER_FILE", self.roster_file),
        ):
            from hackathon.surveillance_skill import SurveillanceSkill

            self.skill = SurveillanceSkill.__new__(SurveillanceSkill)
            self.skill._claude = None
            yield

    def _write_roster(self, roster):
        with open(self.roster_file, "w") as f:
            json.dump(roster, f)

    def _write_observations(self, observations):
        with open(self.obs_file, "w") as f:
            for obs in observations:
                f.write(json.dumps(obs) + "\n")

    def test_load_roster_empty(self):
        assert self.skill._load_roster() == {}

    def test_load_roster(self):
        self._write_roster({"person-1": {"activity": "working"}})
        roster = self.skill._load_roster()
        assert roster["person-1"]["activity"] == "working"

    def test_load_observations_empty(self):
        assert self.skill._load_observations() == []

    def test_load_observations(self):
        obs = [
            {"ts": 1.0, "person_id": "person-1", "activity": "sitting"},
            {"ts": 2.0, "person_id": "person-1", "activity": "standing"},
        ]
        self._write_observations(obs)
        loaded = self.skill._load_observations()
        assert len(loaded) == 2
        assert loaded[0]["activity"] == "sitting"

    def test_load_observations_max_lines(self):
        obs = [{"ts": float(i), "person_id": "p", "activity": f"a{i}"} for i in range(10)]
        self._write_observations(obs)
        loaded = self.skill._load_observations(max_lines=3)
        assert len(loaded) == 3
        assert loaded[0]["activity"] == "a7"  # last 3

    def test_list_people_empty(self):
        result = self.skill.list_people()
        assert "No people" in result

    def test_list_people_with_data(self):
        self._write_roster({
            "person-1": {
                "long_term_id": 1,
                "activity": "working on laptop",
                "last_seen": time.time(),
            },
            "person-2": {
                "long_term_id": 2,
                "activity": "talking on phone",
                "last_seen": time.time() - 300,
            },
        })
        result = self.skill.list_people()
        assert "person-1" in result
        assert "working on laptop" in result
        assert "person-2" in result
        assert "talking on phone" in result

    def test_query_surveillance_no_data(self):
        result = self.skill.query_surveillance("Who is here?")
        assert "No surveillance data" in result

    def test_query_surveillance_calls_claude(self):
        self._write_roster({
            "person-1": {
                "long_term_id": 1,
                "activity": "working",
                "last_seen": time.time(),
            }
        })
        self._write_observations([
            {"ts": time.time(), "time": "13:00:00", "person_id": "person-1", "activity": "working"}
        ])

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Person-1 is currently working.")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        self.skill._claude = mock_client

        result = self.skill.query_surveillance("What is person-1 doing?")
        assert result == "Person-1 is currently working."
        mock_client.messages.create.assert_called_once()

        # Verify the prompt contains our data
        call_args = mock_client.messages.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "person-1" in prompt
        assert "working" in prompt
        assert "What is person-1 doing?" in prompt

    def test_query_surveillance_handles_api_error(self):
        self._write_roster({"person-1": {"activity": "x", "last_seen": time.time(), "long_term_id": 1}})

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API rate limited")
        self.skill._claude = mock_client

        result = self.skill.query_surveillance("test")
        assert "Error" in result
        assert "API rate limited" in result

    def test_get_claude_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove ANTHROPIC_API_KEY if set
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.skill._claude = None
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                self.skill._get_claude()

    def test_get_claude_with_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-123"}):
            self.skill._claude = None
            with patch("hackathon.surveillance_skill.anthropic.Anthropic") as mock_cls:
                mock_cls.return_value = MagicMock()
                client = self.skill._get_claude()
                mock_cls.assert_called_once_with(api_key="test-key-123")
                assert client is not None

    def test_skill_decorator_present(self):
        assert hasattr(self.skill.query_surveillance, "__skill__")
        assert hasattr(self.skill.list_people, "__skill__")
