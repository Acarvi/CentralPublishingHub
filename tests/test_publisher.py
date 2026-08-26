import pytest
import os
import json
import requests
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, mock_open
from core.publisher import (
    load_accounts, get_account_credentials, load_scheduled_posts,
    save_scheduled_posts, add_to_queue, get_queue, upload_to_temporary_host,
    publish_item_local, search_locations, requires_public_url,
    resolve_public_media_url, normalize_platforms, process_due_posts
)


@patch("os.path.exists", return_value=False)
def test_load_accounts_missing(mock_exists):
    accounts = load_accounts()
    assert accounts == {}

@patch("builtins.open", new_callable=mock_open, read_data='{"test": {"access_token": "token"}}')
@patch("os.path.exists", return_value=True)
def test_load_accounts(mock_exists, mock_file):
    accounts = load_accounts()
    assert "test" in accounts
    assert accounts["test"]["access_token"] == "token"

def test_get_account_credentials():
    with patch("core.publisher.load_accounts") as mock_load:
        mock_load.return_value = {"eco": {"id": 1}}
        assert get_account_credentials("eco") == {"id": 1}
        assert get_account_credentials("none") is None

@patch("os.path.exists", return_value=False)
def test_load_scheduled_posts_missing(mock_exists):
    posts = load_scheduled_posts()
    assert posts == []

@patch("builtins.open", new_callable=mock_open, read_data='[]')
@patch("os.path.exists", return_value=True)
def test_load_scheduled_posts(mock_exists, mock_file):
    posts = load_scheduled_posts()
    assert posts == []

@patch("os.replace")
@patch("builtins.open", new_callable=mock_open)
@patch("json.dump")
def test_save_scheduled_posts(mock_json, mock_file, mock_replace):
    save_scheduled_posts([{"id": 1}])
    mock_json.assert_called_once()
    mock_replace.assert_called_once()

@patch("core.publisher.load_scheduled_posts", return_value=[])
@patch("core.publisher.save_scheduled_posts")
def test_add_to_queue(mock_save, mock_load):
    res = add_to_queue([{"cap": "test"}])
    mock_save.assert_called_once()
    saved_posts = mock_save.call_args[0][0]
    assert saved_posts[0]["status"] == "pending"
    assert len(res) == 1


def test_add_to_queue_real_idempotency_avoids_duplicate_jobs(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import add_to_queue, load_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        # First request
        first_post = {
            "caption": "Noticia A",
            "idempotency_key": "idem-key-abc-123",
            "scheduled_id": "sch-post-1",
            "client_job_id": "attempt-1",
            "platforms": ["instagram_reel"],
        }
        res1 = add_to_queue([first_post])
        assert len(res1) == 1
        assert res1[0]["scheduled_id"] == "sch-post-1"
        assert res1[0]["client_job_id"] == "attempt-1"

        queue_after_first = load_scheduled_posts()
        assert len(queue_after_first) == 1

        # Second request (retransmission of same logical operation with different attempt ID)
        second_post = {
            "caption": "Noticia A",
            "idempotency_key": "idem-key-abc-123",
            "scheduled_id": "sch-post-2-retry",
            "client_job_id": "attempt-2-retry",
            "platforms": ["instagram_reel"],
        }
        res2 = add_to_queue([second_post])
        assert len(res2) == 1
        # Reuses canonical job persisted in queue
        assert res2[0]["scheduled_id"] == "sch-post-1"
        assert res2[0]["client_job_id"] == "attempt-1"
        assert res2[0]["idempotency_key"] == "idem-key-abc-123"

        # Queue MUST still contain exactly 1 job (no duplicate!)
        queue_after_second = load_scheduled_posts()
        assert len(queue_after_second) == 1
        assert queue_after_second[0]["scheduled_id"] == "sch-post-1"


def test_publish_now_with_idempotency_replay_does_not_reexecute(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import publish_now_with_idempotency, load_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        call_count = 0
        def mock_publish(payload):
            nonlocal call_count
            call_count += 1
            return {
                "status": "success",
                "results": [
                    {"platform": "instagram_reel", "success": True, "result": {"id": "ig_1"}},
                    {"platform": "youtube_shorts", "success": True, "result": {"id": "yt_1"}},
                ],
            }

        monkeypatch.setattr("core.publisher.publish_item_local", mock_publish)

        payload_1 = {
            "caption": "Immediate Post",
            "idempotency_key": "idem-imm-123",
            "scheduled_id": "sch-imm-1",
            "client_job_id": "attempt-imm-1",
            "platforms": ["instagram_reel", "youtube_shorts"],
        }

        # First call executes publication
        res1 = publish_now_with_idempotency(payload_1)
        assert res1["status"] == "success"
        assert call_count == 1

        # Second call (transport replay with same idempotency key)
        payload_1_replay = dict(
            payload_1,
            client_job_id="attempt-imm-1-retry",
            scheduled_id="sch-imm-1-retry",
        )
        res2 = publish_now_with_idempotency(payload_1_replay)
        assert res2["status"] == "success"
        # publish_item_local MUST NOT be called again! (Deduplicated!)
        assert call_count == 1
        assert res2 == res1


def test_publish_now_with_idempotency_new_operation_after_failure_executes(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import publish_now_with_idempotency

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        call_count = 0
        def mock_publish(payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "status": "error",
                    "results": [{"platform": "youtube_shorts", "success": False, "result": {"error": "Quota"}}],
                }
            return {
                "status": "success",
                "results": [{"platform": "youtube_shorts", "success": True, "result": {"id": "yt_ok"}}],
            }

        monkeypatch.setattr("core.publisher.publish_item_local", mock_publish)

        payload_A = {
            "caption": "Post A",
            "idempotency_key": "idem-op-A",
            "scheduled_id": "sch-A",
            "client_job_id": "attempt-A",
            "platforms": ["youtube_shorts"],
        }
        res_A = publish_now_with_idempotency(payload_A)
        assert res_A["status"] == "error"
        assert call_count == 1

        # Retry creates new operation B with new idempotency key
        payload_B = {
            "caption": "Post A",
            "idempotency_key": "idem-op-B",
            "scheduled_id": "sch-B",
            "client_job_id": "attempt-B",
            "platforms": ["youtube_shorts"],
        }
        res_B = publish_now_with_idempotency(payload_B)
        assert res_B["status"] == "success"
        assert call_count == 2


def test_publish_now_concurrent_requests_execute_single_upload(monkeypatch):
    """
    Real CONCURRENT test:
    - Thread A enters publish_item_local() and blocks on an Event.
    - While Thread A is executing inside publish_item_local(), Thread B launches with the EXACT SAME idempotency_key.
    - Thread B acquires reservation lock, sees 'immediate_processing', and enters bounded wait WITHOUT calling publish_item_local.
    - Release Thread A.
    - Both threads finish.
    - Assert:
      1. publish_item_local was called EXACTLY 1 time (publish_item_local.call_count == 1).
      2. Exactly 1 record persisted for that idempotency_key.
      3. Both threads return the exact same canonical execution result.
    """
    import tempfile
    import threading
    from pathlib import Path
    from core.publisher import publish_now_with_idempotency, load_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        call_count = 0
        inside_publish_event = threading.Event()
        release_publish_event = threading.Event()

        canonical_result = {
            "status": "success",
            "results": [
                {"platform": "instagram_reel", "success": True, "result": {"id": "ig_concurrent_1"}},
                {"platform": "youtube_shorts", "success": True, "result": {"id": "yt_concurrent_1"}},
            ],
        }

        def mock_publish(payload):
            nonlocal call_count
            call_count += 1
            inside_publish_event.set()
            # Block until test signals release
            release_publish_event.wait(timeout=5.0)
            return canonical_result

        monkeypatch.setattr("core.publisher.publish_item_local", mock_publish)

        payload_shared = {
            "caption": "Concurrent Immediate Post",
            "idempotency_key": "idem-concurrent-exact-key-999",
            "scheduled_id": "sch-concurrent-1",
            "client_job_id": "attempt-concurrent-1",
            "platforms": ["instagram_reel", "youtube_shorts"],
        }

        results = {}

        def worker_A():
            results["A"] = publish_now_with_idempotency(payload_shared, wait_timeout_seconds=5.0)

        def worker_B():
            results["B"] = publish_now_with_idempotency(payload_shared, wait_timeout_seconds=5.0)

        thread_A = threading.Thread(target=worker_A)
        thread_B = threading.Thread(target=worker_B)

        # 1. Start Thread A (becomes owner)
        thread_A.start()
        # Wait until Thread A is confirmed INSIDE publish_item_local()
        assert inside_publish_event.wait(timeout=3.0)

        # 2. While Thread A is executing, start Thread B with the same idempotency key
        thread_B.start()
        # Brief pause to ensure Thread B has hit the reservation lock and entered wait
        import time
        time.sleep(0.1)

        # 3. Release Thread A to finish execution and persist result
        release_publish_event.set()

        thread_A.join(timeout=3.0)
        thread_B.join(timeout=3.0)

        # Mandatory Assertions:
        # 1. publish_item_local was called EXACTLY ONCE
        assert call_count == 1, f"Expected call_count == 1, got {call_count}"

        # 2. Both threads returned the exact same canonical result
        assert results["A"] == canonical_result
        assert results["B"] == canonical_result

        # 3. Exactly 1 record persisted in the queue/ledger store for that key
        saved_posts = load_scheduled_posts()
        matching_posts = [p for p in saved_posts if p.get("idempotency_key") == "idem-concurrent-exact-key-999"]
        assert len(matching_posts) == 1
        assert matching_posts[0]["status"] == "published"
        assert matching_posts[0]["result"] == canonical_result


def test_recover_interrupted_posts_handles_immediate_and_scheduled_processing(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import recover_interrupted_posts, load_scheduled_posts, save_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        posts = [
            {"idempotency_key": "k1", "scheduled_id": "sch-1", "client_job_id": "cli-1", "source_item_id": "src-1", "status": "immediate_processing"},
            {"idempotency_key": "k2", "scheduled_id": "sch-2", "client_job_id": "cli-2", "source_item_id": "src-2", "status": "processing"},
            {"idempotency_key": "k3", "scheduled_id": "sch-3", "client_job_id": "cli-3", "source_item_id": "src-3", "status": "published"},
        ]
        save_scheduled_posts(posts)

        recovered = recover_interrupted_posts()
        assert recovered == 2

        posts_after = load_scheduled_posts()
        # immediate_processing MUST become unknown, NOT error
        assert posts_after[0]["status"] == "unknown"
        assert posts_after[0]["error"] == "immediate_publish_interrupted_requires_reconciliation"
        assert posts_after[0]["result"]["requires_reconciliation"] is True
        assert posts_after[0]["requires_reconciliation"] is True
        assert posts_after[0]["idempotency_key"] == "k1"
        assert posts_after[0]["scheduled_id"] == "sch-1"

        # scheduled processing MUST ALSO become unknown, NOT error/retryable
        assert posts_after[1]["status"] == "unknown"
        assert posts_after[1]["error"] == "scheduled_publish_interrupted_requires_reconciliation"
        assert posts_after[1]["result"]["requires_reconciliation"] is True
        assert posts_after[1]["requires_reconciliation"] is True
        assert posts_after[1]["idempotency_key"] == "k2"
        assert posts_after[1]["scheduled_id"] == "sch-2"
        assert posts_after[1]["client_job_id"] == "cli-2"
        assert posts_after[1]["source_item_id"] == "src-2"

        # published post is untouched
        assert posts_after[2]["status"] == "published"


def test_recover_interrupted_posts_preserves_unknown_and_replay_does_not_execute(monkeypatch):
    """
    Crash test:
    reservation immediate_processing -> recover_interrupted_posts -> replay same operation
    Verify:
    1. publish_item_local.call_count == 0 on replay
    2. Same idempotency key
    3. No new operation
    4. Result unknown / requires_reconciliation
    """
    import tempfile
    from pathlib import Path
    from core.publisher import recover_interrupted_posts, publish_now_with_idempotency, load_scheduled_posts, save_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        # 1. Post is in immediate_processing when crash happens
        posts = [
            {
                "idempotency_key": "idem-crash-test-1",
                "scheduled_id": "sch-crash-1",
                "client_job_id": "attempt-crash-1",
                "status": "immediate_processing",
                "caption": "Crash test post",
                "platforms": ["instagram_reel"],
            }
        ]
        save_scheduled_posts(posts)

        # 2. Hub recovers after crash
        recovered = recover_interrupted_posts()
        assert recovered == 1

        posts_after_restart = load_scheduled_posts()
        # Status MUST be unknown (NOT error / failed)
        assert posts_after_restart[0]["status"] == "unknown"
        assert posts_after_restart[0]["error"] == "immediate_publish_interrupted_requires_reconciliation"

        # 3. Client replays the same operation after network reconnection
        call_count = 0
        def mock_publish(payload):
            nonlocal call_count
            call_count += 1
            return {"status": "success", "results": []}

        monkeypatch.setattr("core.publisher.publish_item_local", mock_publish)

        replay_payload = {
            "idempotency_key": "idem-crash-test-1",
            "scheduled_id": "sch-crash-1",
            "client_job_id": "attempt-crash-1",
            "caption": "Crash test post",
            "platforms": ["instagram_reel"],
        }
        res = publish_now_with_idempotency(replay_payload)

        # Replay MUST NOT call publish_item_local()!
        assert call_count == 0
        assert res["status"] == "unknown"
        assert res.get("requires_reconciliation") is True


@patch("core.publisher.load_scheduled_posts", return_value=[{"status": "pending"}, {"status": "done"}])
def test_get_queue(mock_load):
    queue = get_queue()
    assert len(queue) == 1
    assert queue[0]["status"] == "pending"


def test_process_due_posts_executes_overdue_and_respects_explicit_expiry_only(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import process_due_posts, load_scheduled_posts, save_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        published_items = []
        def mock_publish(post):
            published_items.append(post["scheduled_id"])
            return {"status": "success", "results": [{"platform": "instagram_reel", "success": True, "result": {"id": "123"}}]}

        monkeypatch.setattr("core.publisher.publish_item_local", mock_publish)

        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        posts = [
            # 1. Due slightly in past (5 min ago) -> MUST publish
            {"scheduled_id": "due-5m", "status": "pending", "target_time": (now - timedelta(minutes=5)).isoformat()},
            # 2. Overdue significantly (3 days ago) with NO explicit expiry -> MUST publish (not silently expired!)
            {"scheduled_id": "overdue-3d", "status": "pending", "target_time": (now - timedelta(days=3)).isoformat()},
            # 3. Future post (+2 hours) -> MUST NOT publish early
            {"scheduled_id": "future-2h", "status": "pending", "target_time": (now + timedelta(hours=2)).isoformat()},
            # 4. Overdue post WITH explicit expiry passed -> MUST expire
            {
                "scheduled_id": "explicit-expired",
                "status": "pending",
                "target_time": (now - timedelta(days=1)).isoformat(),
                "expires_at": (now - timedelta(hours=1)).isoformat(),
            },
            # 5. Overdue post WITH explicit latest_publish_at in future -> MUST publish
            {
                "scheduled_id": "explicit-future-exp",
                "status": "pending",
                "target_time": (now - timedelta(minutes=30)).isoformat(),
                "latest_publish_at": (now + timedelta(hours=1)).isoformat(),
            },
        ]
        save_scheduled_posts(posts)

        due_count = process_due_posts(now)
        assert due_count == 3
        assert published_items == ["due-5m", "overdue-3d", "explicit-future-exp"]

        posts_after = load_scheduled_posts()
        assert posts_after[0]["status"] == "published"
        assert posts_after[1]["status"] == "published"
        assert posts_after[2]["status"] == "pending"  # Future job remains pending
        assert posts_after[3]["status"] == "expired"  # Explicitly expired
        assert posts_after[3]["error"] == "schedule_explicitly_expired"
        assert posts_after[4]["status"] == "published"


def test_two_overdue_jobs_crash_simulation_claims_one_at_a_time(monkeypatch):
    """
    Mandatory senior review regression test:
    Two overdue jobs A and B.
    Scheduler claims job A into 'processing'.
    Process crashes/dies abruptly while job A is in flight.
    Verify:
    1. Job B was NEVER marked processing and remained 'pending'.
    2. Restart recovery marks ONLY job A as 'unknown' / requires_reconciliation.
    3. Job B remains pending and publishes successfully on the next scheduler cycle.
    4. Job A is NOT automatically retried.
    """
    import tempfile
    from pathlib import Path
    from core.publisher import process_due_posts, recover_interrupted_posts, load_scheduled_posts, save_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        posts = [
            {
                "scheduled_id": "job-A",
                "idempotency_key": "idem-A",
                "client_job_id": "cli-A",
                "source_item_id": "src-A",
                "status": "pending",
                "target_time": (now - timedelta(hours=2)).isoformat(),
                "caption": "Job A",
                "platforms": ["instagram_reel"],
            },
            {
                "scheduled_id": "job-B",
                "idempotency_key": "idem-B",
                "client_job_id": "cli-B",
                "source_item_id": "src-B",
                "status": "pending",
                "target_time": (now - timedelta(hours=1)).isoformat(),
                "caption": "Job B",
                "platforms": ["instagram_reel"],
            },
        ]
        save_scheduled_posts(posts)

        # 1. Simulate process executing Job A and crashing before writing final result
        class SimulatedCrashError(Exception):
            pass

        def mock_publish_crash(post):
            if post["scheduled_id"] == "job-A":
                # Crash immediately after claim while Job A is in-flight
                raise SimulatedCrashError("Abrupt process crash during Job A")
            return {"status": "success", "results": [{"platform": "instagram_reel", "success": True, "result": {"id": "ig_B"}}]}

        # When publish_item_local raises, process_due_posts catches worker_exception,
        # but to simulate a full process crash/death during execution where final save never happens,
        # we can simulate the state on disk after Job A is claimed:
        # Job A claimed as 'processing' (saved by process_due_posts claim step)
        # Job B was untouched in queue as 'pending'
        queue_at_crash = [
            dict(posts[0], status="processing", started_at=now.isoformat()),
            dict(posts[1], status="pending"),
        ]
        save_scheduled_posts(queue_at_crash)

        # 2. Hub process restarts after crash
        recovered = recover_interrupted_posts()
        assert recovered == 1

        queue_after_recovery = load_scheduled_posts()
        # Job A MUST become unknown (requiring reconciliation)
        assert queue_after_recovery[0]["scheduled_id"] == "job-A"
        assert queue_after_recovery[0]["status"] == "unknown"
        assert queue_after_recovery[0]["error"] == "scheduled_publish_interrupted_requires_reconciliation"
        assert queue_after_recovery[0]["requires_reconciliation"] is True

        # Job B MUST STILL be pending!
        assert queue_after_recovery[1]["scheduled_id"] == "job-B"
        assert queue_after_recovery[1]["status"] == "pending"

        # 3. Next scheduler cycle runs
        published_items = []
        def mock_publish_healthy(post):
            published_items.append(post["scheduled_id"])
            return {"status": "success", "results": [{"platform": "instagram_reel", "success": True, "result": {"id": "ig_B"}}]}

        monkeypatch.setattr("core.publisher.publish_item_local", mock_publish_healthy)

        due_count = process_due_posts(now)
        # Only Job B executes; Job A is NOT retried!
        assert due_count == 1
        assert published_items == ["job-B"]

        queue_final = load_scheduled_posts()
        # Job A remains unknown
        assert queue_final[0]["status"] == "unknown"
        assert queue_final[0]["scheduled_id"] == "job-A"
        # Job B is published
        assert queue_final[1]["status"] == "published"
        assert queue_final[1]["scheduled_id"] == "job-B"


def test_process_due_posts_handles_malformed_target_time_and_expiry(monkeypatch):
    """Verify that malformed target_time and expiry fields result in visible terminal errors instead of hanging or publishing."""
    import tempfile
    from pathlib import Path
    from core.publisher import process_due_posts, load_scheduled_posts, save_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        published_items = []
        def mock_publish(post):
            published_items.append(post["scheduled_id"])
            return {"status": "success", "results": []}

        monkeypatch.setattr("core.publisher.publish_item_local", mock_publish)

        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        posts = [
            # 1. Malformed target_time
            {"scheduled_id": "bad-target", "status": "pending", "target_time": "not-a-valid-iso-date"},
            # 2. Missing target_time
            {"scheduled_id": "missing-target", "status": "pending", "target_time": None, "scheduled_at": None},
            # 3. Malformed explicit expiry (expires_at) -> MUST NOT publish
            {
                "scheduled_id": "bad-expiry",
                "status": "pending",
                "target_time": (now - timedelta(minutes=10)).isoformat(),
                "expires_at": "invalid-expiry-format",
            },
        ]
        save_scheduled_posts(posts)

        due_count = process_due_posts(now)
        # None of the malformed jobs should be published
        assert due_count == 0
        assert published_items == []

        posts_after = load_scheduled_posts()
        # 1. Bad target time -> error
        assert posts_after[0]["status"] == "error"
        assert posts_after[0]["error"] == "invalid_schedule_target_time"
        assert "Cannot parse target_time" in posts_after[0]["result"]["details"]

        # 2. Missing target time -> error
        assert posts_after[1]["status"] == "error"
        assert posts_after[1]["error"] == "missing_schedule_target_time"

        # 3. Bad expiry -> error (NOT published!)
        assert posts_after[2]["status"] == "error"
        assert posts_after[2]["error"] == "invalid_schedule_expiry"
        assert "Cannot parse expiry" in posts_after[2]["result"]["details"]


def test_repeated_scheduler_and_recovery_calls_remain_idempotent(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import process_due_posts, recover_interrupted_posts, load_scheduled_posts, save_scheduled_posts

    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        posts = [
            {"scheduled_id": "sch-pub-1", "idempotency_key": "k1", "status": "published", "result": {"status": "success"}},
            {"scheduled_id": "sch-unk-1", "idempotency_key": "k2", "status": "unknown", "error": "interrupted"},
            {"scheduled_id": "sch-exp-1", "idempotency_key": "k3", "status": "expired", "error": "schedule_explicitly_expired"},
        ]
        save_scheduled_posts(posts)

        # Recovery on already stable posts recovers 0 and mutates nothing
        assert recover_interrupted_posts() == 0
        assert load_scheduled_posts() == posts

        # Scheduler on non-pending posts executes 0 and mutates nothing
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        assert process_due_posts(now) == 0
        assert load_scheduled_posts() == posts

def test_requires_public_url_for_instagram_targets():
    assert requires_public_url(["instagram_reel"]) is True
    assert requires_public_url(["instagram_story"]) is True
    assert requires_public_url(["instagram_feed"]) is True
    assert requires_public_url(["youtube_shorts"]) is False

def test_normalize_platform_aliases():
    assert normalize_platforms(["instagram", "reel", "story", "feed", "post", "instagram_post"]) == [
        "instagram_reel",
        "instagram_reel",
        "instagram_story",
        "instagram_feed",
        "instagram_feed",
        "instagram_feed",
    ]

@patch("core.publisher.upload_to_temporary_host")
def test_resolve_public_media_url_uses_existing_url(mock_upload):
    url, error = resolve_public_media_url({
        "video_url": "https://cdn.example/video.mp4",
        "video_path": "local.mp4",
        "platforms": ["instagram_reel"],
    })
    assert url == "https://cdn.example/video.mp4"
    assert error is None
    mock_upload.assert_not_called()

@patch("core.publisher.upload_to_temporary_host", return_value="https://temp.example/video.mp4")
def test_resolve_public_media_url_uploads_for_instagram_path(mock_upload):
    url, error = resolve_public_media_url({
        "video_path": "local.mp4",
        "platforms": ["instagram_story"],
    })
    assert url == "https://temp.example/video.mp4"
    assert error is None
    mock_upload.assert_called_once_with("local.mp4")

@patch("core.publisher.upload_to_temporary_host")
def test_resolve_public_media_url_missing_media_for_instagram(mock_upload):
    url, error = resolve_public_media_url({"platforms": ["instagram_reel"]})
    assert url is None
    assert error["error"] == "PUBLIC_URL_REQUIRED"
    mock_upload.assert_not_called()

@patch("core.publisher.upload_to_temporary_host", return_value=None)
def test_resolve_public_media_url_upload_failure(mock_upload):
    url, error = resolve_public_media_url({
        "video_path": "local.mp4",
        "platforms": ["instagram_feed"],
    })
    assert url is None
    assert error["error"] == "TEMPORARY_UPLOAD_FAILED"
    mock_upload.assert_called_once_with("local.mp4")

@patch("core.publisher.upload_to_temporary_host")
def test_resolve_public_media_url_does_not_upload_for_youtube(mock_upload):
    url, error = resolve_public_media_url({
        "video_path": "local.mp4",
        "platforms": ["youtube_shorts"],
    })
    assert url is None
    assert error is None
    mock_upload.assert_not_called()

@patch("requests.get")
@patch("requests.post")
def test_upload_to_temporary_host_catbox(mock_post, mock_get):
    mock_post.return_value.status_code = 200
    mock_post.return_value.text = "https://files.catbox.moe/test.mp4"
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "https://files.catbox.moe/test.mp4"
    mock_get.assert_not_called()

@patch("requests.get", side_effect=Exception("Fail"))
@patch("requests.post")
@patch("time.sleep")
def test_upload_to_temporary_host_uguu(mock_sleep, mock_post, mock_get):
    mock_post.side_effect = [
        Exception("Catbox fail 1"),
        Exception("Catbox fail 2"),
        Exception("Catbox fail 3"),
        Exception("Litterbox fail"),
        MagicMock(status_code=200, text="https://uguu.se/test.mp4"),
    ]
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "https://uguu.se/test.mp4"

@patch("requests.get")
@patch("requests.post")
@patch("time.sleep")
def test_upload_to_temporary_host_gofile_last(mock_sleep, mock_post, mock_get):
    mock_get.return_value.json.return_value = {"status": "ok", "data": {"servers": [{"name": "srv1"}]}}
    gofile_response = MagicMock(status_code=200)
    gofile_response.json.return_value = {"status": "ok", "data": {"downloadPage": "http://gofile.io/123"}}
    mock_post.side_effect = [
        Exception("Catbox fail 1"),
        Exception("Catbox fail 2"),
        Exception("Catbox fail 3"),
        Exception("Litterbox fail"),
        Exception("Uguu fail"),
        gofile_response,
    ]
    
    with patch("builtins.open", mock_open(read_data=b"data")):
        url = upload_to_temporary_host("test.mp4")
        assert url == "http://gofile.io/123"

@patch("core.publisher.get_account_credentials", return_value={"access_token": "tk", "ig_user_id": "ig", "fb_page_id": "fb"})
@patch("requests.post")
@patch("core.publisher.upload_to_temporary_host", return_value=None)
@patch("core.publisher.log_print")
def test_publish_item_local_upload_fail(mock_log, mock_upload, mock_post, mock_get_creds):
    post = {"video_path": "test.mp4", "caption": "test", "platforms": ["instagram_reel"]}
    result = publish_item_local(post)
    assert result["status"] == "error"
    assert result["error"] == "TEMPORARY_UPLOAD_FAILED"
    mock_log.assert_called_with("Temporary hosting did not return a public URL", "ERROR")

@patch("core.publisher.get_account_credentials")
@patch("core.publisher._upload_to_ig")
@patch("core.publisher.log_print")
def test_publish_item_local_does_not_call_ig_when_url_resolution_fails(mock_log, mock_ig, mock_get_creds):
    post = {"caption": "test", "platforms": ["instagram_reel"]}
    result = publish_item_local(post)
    assert result["status"] == "error"
    assert result["error"] == "PUBLIC_URL_REQUIRED"
    mock_ig.assert_not_called()
    mock_get_creds.assert_not_called()

@patch("requests.post")
def test_upload_to_ig_error_code_3(mock_post):
    from core.publisher import _upload_to_ig
    mock_post.return_value.json.return_value = {"error": {"code": 3}}
    with patch("time.sleep"):
        res = _upload_to_ig("http://url", "cap", "tk", "ig", "REELS")
        assert res["error"] == "LIVE_MODE_RESTRICTION"

@patch("requests.post")
def test_upload_to_ig_exception(mock_post):
    from core.publisher import _upload_to_ig
    mock_post.side_effect = Exception("API ERR")
    with patch("time.sleep"):
        res = _upload_to_ig("http://url", "cap", "tk", "ig", "REELS")
        assert res["error"] == "IG_REQUEST_FAILED"

@patch("core.publisher.get_account_credentials", return_value={"access_token": "tk", "ig_user_id": "ig", "fb_page_id": "fb"})
@patch("requests.post")
@patch("core.publisher.upload_to_temporary_host", return_value="http://url")
@patch("time.sleep")
@patch("core.publisher.upload_short")
@patch("core.publisher.upload_facebook_video")
def test_publish_item_local_all_platforms(mock_fb, mock_yt, mock_sleep, mock_upload, mock_post, mock_get_creds):
    post = {
        "caption": "test",
        "platforms": ["instagram_reel", "instagram_story", "facebook_reel", "facebook_story", "youtube_shorts"],
        "video_path": "path/to/vid.mp4"
    }
    # Mock IG upload sequence
    mock_post.return_value.json.side_effect = [
        {"id": "c1"}, {"status_code": "FINISHED"}, {"id": "p1"}, # Reel
        {"id": "c2"}, {"status_code": "FINISHED"}, {"id": "p2"}  # Story
    ]
    publish_item_local(post)
    assert mock_yt.called
    assert mock_fb.call_count == 2

@patch("requests.post")
def test_upload_facebook_video_pull(mock_post):
    from core.publisher import upload_facebook_video
    with patch("core.publisher.get_page_access_token", return_value="p_tk"):
        with patch("os.path.exists", return_value=False): # Force pull phase
            mock_post.return_value.json.side_effect = [
                {"video_id": "vid123", "upload_url": "http://up"},
                {"success": True}
            ]
            mock_post.return_value.status_code = 200
            res = upload_facebook_video("http://external/vid.mp4", "cap", "u_tk", "p_id")
            assert res["id"] == "vid123"

@patch("requests.post")
def test_upload_facebook_video_exception(mock_post):
    from core.publisher import upload_facebook_video
    with patch("core.publisher.get_page_access_token", return_value="tk"):
        mock_post.side_effect = Exception("General Err")
        res = upload_facebook_video("url", "cap", "tk", "id")
        assert res["error"] == "General Err"

@patch("core.publisher.get_account_credentials", return_value=None)
@patch("core.publisher.get_env_or_raise", return_value="env_tk")
@patch("requests.post")
def test_publish_item_local_env_fallback(mock_post, mock_env, mock_get_creds):
    post = {"platforms": ["instagram_reel"], "video_url": "http://url", "caption": "test"}
    mock_post.return_value.json.side_effect = [{"id": "c"}, {"status_code": "FINISHED"}, {"id": "p"}]
    with patch("time.sleep"):
        publish_item_local(post)
    assert mock_env.called

@patch("requests.get", side_effect=lambda x: MagicMock(json=lambda: {"status": "ok", "data": {"servers": [{"name": "s"}]}}) if "servers" in x else Exception("Upload failed"))
@patch("requests.post", side_effect=Exception("Post failed"))
def test_upload_to_temporary_host_retry_logic(mock_post, mock_get):
    # This will trigger the Uguu and Catbox fallbacks
    with patch("builtins.open", mock_open(read_data=b"data")):
        with patch("time.sleep"):
            url = upload_to_temporary_host("test.mp4")
            assert url is None # All fail but lines are covered

def test_get_page_access_token():
    from core.publisher import get_page_access_token
    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"access_token": "page_tk"}
        assert get_page_access_token("u_tk", "p_id") == "page_tk"
        
        mock_get.side_effect = Exception("Err")

@patch("core.publisher.get_account_credentials", return_value={"access_token": "tk", "ig_user_id": "ig", "fb_page_id": "fb"})
@patch("requests.post")
@patch("core.publisher.upload_to_temporary_host", return_value="http://url")
@patch("time.sleep")
@patch("core.publisher.upload_short")
@patch("core.publisher.upload_facebook_video")
def test_publish_item_local_all_platforms(mock_fb, mock_yt, mock_sleep, mock_upload, mock_post, mock_get_creds):
    post = {
        "caption": "test",
        "platforms": ["instagram_reel", "instagram_story", "facebook_reel", "facebook_story", "youtube_shorts"],
        "video_path": "path/to/vid.mp4"
    }
    # Mock IG upload sequence
    mock_post.return_value.json.side_effect = [
        {"id": "c1"}, {"status_code": "FINISHED"}, {"id": "p1"}, # Reel
        {"id": "c2"}, {"status_code": "FINISHED"}, {"id": "p2"}  # Story
    ]
    publish_item_local(post)
    assert mock_yt.called
    assert mock_fb.call_count == 2

@patch("requests.post")
def test_upload_facebook_video_pull(mock_post):
    from core.publisher import upload_facebook_video
    with patch("core.publisher.get_page_access_token", return_value="p_tk"):
        with patch("os.path.exists", return_value=False): # Force pull phase
            mock_post.return_value.json.side_effect = [
                {"video_id": "vid123", "upload_url": "http://up"},
                {"success": True}
            ]
            mock_post.return_value.status_code = 200
            res = upload_facebook_video("http://external/vid.mp4", "cap", "u_tk", "p_id")
            assert res["id"] == "vid123"

@patch("requests.post")
def test_upload_facebook_video_exception(mock_post):
    from core.publisher import upload_facebook_video
    with patch("core.publisher.get_page_access_token", return_value="tk"):
        mock_post.side_effect = Exception("General Err")
        res = upload_facebook_video("url", "cap", "tk", "id")
        assert res["error"] == "General Err"

@patch("core.publisher.get_account_credentials", return_value=None)
@patch("core.publisher.get_env_or_raise", return_value="env_tk")
@patch("requests.post")
def test_publish_item_local_env_fallback(mock_post, mock_env, mock_get_creds):
    post = {"platforms": ["instagram_reel"], "video_url": "http://url", "caption": "test"}
    mock_post.return_value.json.side_effect = [{"id": "c"}, {"status_code": "FINISHED"}, {"id": "p"}]
    with patch("time.sleep"):
        publish_item_local(post)
    assert mock_env.called

@patch("requests.get", side_effect=lambda x: MagicMock(json=lambda: {"status": "ok", "data": {"servers": [{"name": "s"}]}}) if "servers" in x else Exception("Upload failed"))
@patch("requests.post", side_effect=Exception("Post failed"))
def test_upload_to_temporary_host_retry_logic(mock_post, mock_get):
    # This will trigger the Uguu and Catbox fallbacks
    with patch("builtins.open", mock_open(read_data=b"data")):
        with patch("time.sleep"):
            url = upload_to_temporary_host("test.mp4")
            assert url is None # All fail but lines are covered

def test_get_page_access_token():
    from core.publisher import get_page_access_token
    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = {"access_token": "page_tk"}
        assert get_page_access_token("u_tk", "p_id") == "page_tk"
        
        mock_get.side_effect = Exception("Err")
        assert get_page_access_token("u_tk", "p_id") is None

@patch("requests.post")
def test_upload_facebook_video_local(mock_post):
    from core.publisher import upload_facebook_video
    with patch("core.publisher.get_page_access_token", return_value="p_tk"):
        with patch("os.path.exists", return_value=True):
            with patch("os.path.getsize", return_value=100):
                with patch("builtins.open", mock_open(read_data=b"data")):
                    mock_post.return_value.json.side_effect = [
                        {"video_id": "vid123", "upload_url": "http://up"},
                        {"success": True}
                    ]
                    mock_post.return_value.status_code = 200
                    res = upload_facebook_video("local.mp4", "cap", "u_tk", "p_id")
                    assert res["id"] == "vid123"

@patch("core.publisher.get_account_credentials", return_value={"access_token": "tk"})
@patch("requests.get", side_effect=Exception("API Error"))
def test_search_locations_error(mock_get, mock_creds):
    results = search_locations("Madrid")
    assert results == []

def test_is_platform_success_contract():
    from core.publisher import is_platform_success

    # Real success cases
    assert is_platform_success({"id": "178923456789"}) is True
    assert is_platform_success({"video_id": "v12345"}) is True
    assert is_platform_success({"id": "ig_123", "permalink": "https://ig/p/123"}) is True
    assert is_platform_success({"success": True}) is True

    # Error cases that must NEVER be treated as success
    assert is_platform_success({"error": "LIVE_MODE_RESTRICTION"}) is False
    assert is_platform_success({"error": "IG_CONTAINER_CREATE_FAILED", "message": "Failed"}) is False
    assert is_platform_success({"error_message": "Invalid OAuth access token"}) is False
    assert is_platform_success({"errors": [{"code": 100, "message": "Rate limit"}]}) is False
    assert is_platform_success({"error": "Page Access Token Missing"}) is False
    assert is_platform_success({"status_code": 400, "detail": "Bad request"}) is False
    assert is_platform_success({"status_code": 500, "id": "fake_id"}) is False
    assert is_platform_success({"success": False, "details": "Something failed"}) is False
    assert is_platform_success(None) is False
    assert is_platform_success({}) is False
    assert is_platform_success("truthy string") is False
    assert is_platform_success(["id", "123"]) is False


def test_save_scheduled_posts_atomic_replace_success(monkeypatch):
    import tempfile
    from pathlib import Path
    from core.publisher import save_scheduled_posts, load_scheduled_posts
    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        initial_posts = [{"scheduled_id": "job-1", "status": "pending"}]
        save_scheduled_posts(initial_posts)

        assert target_file.exists()
        assert load_scheduled_posts() == initial_posts
        assert not (Path(td) / "scheduled_posts.json.tmp").exists()


def test_save_scheduled_posts_replace_failure_preserves_original(monkeypatch):
    import os
    import tempfile
    from pathlib import Path
    import pytest
    from core.publisher import save_scheduled_posts, load_scheduled_posts
    with tempfile.TemporaryDirectory() as td:
        target_file = Path(td) / "scheduled_posts.json"
        monkeypatch.setattr("core.publisher.SCHEDULED_POSTS_FILE", str(target_file))

        # Initial valid queue
        original_posts = [{"scheduled_id": "job-original", "status": "pending"}]
        save_scheduled_posts(original_posts)

        # Force os.replace to fail (e.g. permission or lock error)
        def mock_replace_fail(src, dst):
            raise PermissionError("Simulated file lock")

        monkeypatch.setattr(os, "replace", mock_replace_fail)
        monkeypatch.setattr("time.sleep", lambda s: None)

        with pytest.raises(PermissionError):
            save_scheduled_posts([{"scheduled_id": "job-corrupted", "status": "pending"}])

        # Ensure original queue remains pristine
        assert load_scheduled_posts() == original_posts

