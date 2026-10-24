import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from looked.database import Database
from looked.errors import AppError
from looked.images import validate_image
from looked.quota import QuotaManager
from looked.service import SearchService


@pytest.mark.parametrize("limit_name", ["monthly_limit", "daily_limit", "hourly_limit"])
def test_zero_limit_blocks_before_provider(setup_service, image_bytes, limit_name):
    settings, db, _, client, _ = setup_service
    settings = replace(settings, **{limit_name: 0})
    quota = QuotaManager(db, settings)
    service = SearchService(db, quota, client, settings)
    with pytest.raises(AppError, match="limit") as error:
        service.run(file_bytes=image_bytes)
    assert error.value.code == "local_quota"
    assert not client.calls
    assert quota.snapshot()["month"]["requests"] == 0


def test_cache_hit_survives_restart_no_key_and_zero_quota(setup_service, image_bytes):
    settings, db, quota, client, service = setup_service
    first = service.run(file_bytes=image_bytes, filename="mock.png")
    restarted_settings = replace(settings, api_key="", monthly_limit=0)
    reopened_db = Database(settings.db_path)
    restarted_quota = QuotaManager(reopened_db, restarted_settings)
    restarted = SearchService(reopened_db, restarted_quota, client, restarted_settings)
    second = restarted.run(file_bytes=image_bytes)
    assert first["id"] == second["id"] and second["cached"]
    assert len(client.calls) == 1
    assert quota.snapshot()["month"]["used"] == 1
    assert quota.snapshot()["month"]["requests"] == 2


def test_parameters_are_part_of_cache_key(setup_service, image_bytes):
    _, _, quota, client, service = setup_service
    one = service.run(file_bytes=image_bytes, values={"hl": "en"})
    two = service.run(file_bytes=image_bytes, values={"hl": "vi"})
    assert one["id"] != two["id"]
    assert len(client.calls) == 2 and quota.snapshot()["month"]["used"] == 2


def test_refresh_counts_and_same_operation_is_idempotent(setup_service, image_bytes):
    _, _, quota, client, service = setup_service
    one = service.run(file_bytes=image_bytes)
    operation = str(uuid.uuid4())
    two = service.run(refresh_id=one["id"], operation_id=operation)
    three = service.run(refresh_id=one["id"], operation_id=operation)
    assert not two["cached"] and three["cached"]
    assert len(client.calls) == 2 and client.calls[-1]["refresh"]
    assert quota.snapshot()["month"]["used"] == 2


def test_clear_history_preserves_usage(setup_service, image_bytes):
    _, db, quota, _, service = setup_service
    service.run(file_bytes=image_bytes)
    before = quota.snapshot()
    db.clear_history()
    assert not db.history() and quota.snapshot() == before
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM url_images").fetchone()[0] == 0


def test_concurrent_clicks_send_one_search_across_db_instances(setup_service, image_bytes):
    settings, db, quota, client, service = setup_service
    client.entered, client.release = threading.Event(), threading.Event()
    second_db = Database(settings.db_path)
    other = SearchService(second_db, QuotaManager(second_db, settings), client, settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.run, file_bytes=image_bytes)
        assert client.entered.wait(5)
        try:
            with pytest.raises(AppError) as error:
                other.run(file_bytes=image_bytes)
            assert error.value.code == "busy"
        finally:
            client.release.set()
        result = first.result(timeout=5)
    assert len(client.calls) == 1 and result["result"]["results"]
    assert quota.snapshot()["month"]["used"] == 1
    assert other.run(file_bytes=image_bytes)["cached"]
    assert len(client.calls) == 1


def test_concurrent_reservations_cannot_overspend(setup_service):
    settings, db, _, _, _ = setup_service
    quota = QuotaManager(db, replace(settings, monthly_limit=1))
    def reserve():
        try:
            return quota.reserve()
        except AppError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(lambda _: reserve(), range(8)))
    assert sum(bool(item) for item in reservations) == 1
    assert quota.snapshot()["month"]["used"] == 1


def test_period_rollover_keeps_old_counters(setup_service):
    settings, db, _, _, _ = setup_service
    clock = [datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)]
    quota = QuotaManager(db, replace(settings, monthly_limit=1), now=lambda: clock[0])
    quota.reserve()
    with pytest.raises(AppError):
        quota.reserve()
    clock[0] = datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert all(period["used"] == 0 for period in quota.snapshot().values())
    quota.reserve()
    assert quota.snapshot()["month"]["used"] == 1
    with db.connect() as conn:
        assert conn.execute("SELECT searches FROM usage WHERE period='month' AND bucket='2026-12'").fetchone()[0] == 1


def test_hour_and_day_rollover_do_not_reset_month(setup_service):
    settings, db, _, _, _ = setup_service
    clock = [datetime(2026, 10, 23, 23, 59, tzinfo=timezone.utc)]
    quota = QuotaManager(db, settings, now=lambda: clock[0])
    quota.reserve()
    clock[0] = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
    snapshot = quota.snapshot()
    assert snapshot["hour"]["used"] == snapshot["day"]["used"] == 0
    assert snapshot["month"]["used"] == 1


def test_url_cache_no_download_until_refresh(setup_service, image_bytes):
    settings, db, quota, client, _ = setup_service
    downloads = []
    def download(url):
        downloads.append(url)
        return validate_image(image_bytes)
    service = SearchService(db, quota, client, settings, downloader=download)
    one = service.run(image_url="https://example.com/mock.png")
    assert service.run(image_url="https://example.com/mock.png")["cached"]
    assert len(downloads) == len(client.calls) == 1
    service.run(refresh_id=one["id"])
    assert len(downloads) == len(client.calls) == 2
    assert client.calls[-1]["image"] is None


def test_file_then_same_url_refreshes_latest_url_source(setup_service, image_bytes):
    settings, db, quota, client, service = setup_service
    file_result = service.run(file_bytes=image_bytes)
    downloads = []
    def download(url):
        downloads.append(url)
        return validate_image(image_bytes)
    service.downloader = download
    url_result = service.run(image_url="https://example.com/mock.png")
    assert file_result["id"] == url_result["id"] and url_result["cached"]
    service.run(refresh_id=url_result["id"])
    assert len(downloads) == 2 and client.calls[-1]["image_url"] == "https://example.com/mock.png"


def test_empty_response_is_cached(setup_service, image_bytes):
    _, _, quota, client, service = setup_service
    client.payload = {"search_metadata": {"status": "Success"}, "visual_matches": []}
    assert service.run(file_bytes=image_bytes)["result"]["results"] == []
    assert service.run(file_bytes=image_bytes)["cached"]
    assert len(client.calls) == 1 and quota.snapshot()["month"]["used"] == 1


def test_failed_refresh_keeps_previous_results(setup_service, image_bytes):
    _, db, quota, client, service = setup_service
    original = service.run(file_bytes=image_bytes)
    client.error = AppError("timeout", "Mock timeout", 504)
    with pytest.raises(AppError):
        service.run(refresh_id=original["id"])
    assert db.get(original["id"])["result"] == original["result"]
    assert quota.snapshot()["month"]["used"] == 2


def test_changed_remote_image_gets_new_hash_on_refresh(setup_service, image_bytes):
    from io import BytesIO
    from PIL import Image
    _, db, quota, client, service = setup_service
    remote = [validate_image(image_bytes)]
    service.downloader = lambda url: remote[0]
    original = service.run(image_url="https://example.com/changing.png")
    output = BytesIO()
    Image.new("RGB", (12, 12), "orange").save(output, "PNG")
    remote[0] = validate_image(output.getvalue())
    assert service.run(image_url="https://example.com/changing.png")["id"] == original["id"]
    fresh = service.run(refresh_id=original["id"])
    assert fresh["id"] != original["id"] and fresh["image_hash"] != original["image_hash"]
    assert service.run(image_url="https://example.com/changing.png")["id"] == fresh["id"]
    assert len(client.calls) == 2 and len(db.history()) == 2


def test_expired_crash_lease_recovers_without_resetting_usage(setup_service, image_bytes):
    _, db, quota, client, service = setup_service
    quota.reserve()  # simulate an attempt that was interrupted by a crash
    with db.connect(write=True) as conn:
        conn.execute("INSERT INTO lease VALUES (1, 'MOCK_crashed_process', 1)")
    service.run(file_bytes=image_bytes)
    assert quota.snapshot()["month"]["used"] == 2 and len(client.calls) == 1


@pytest.mark.parametrize("code,status", [("timeout", 504), ("provider_quota", 429), ("invalid_key", 401), ("malformed_response", 502)])
def test_failed_attempt_stays_counted_and_retry_id_not_replayed(setup_service, image_bytes, code, status):
    _, db, quota, client, service = setup_service
    client.error = AppError(code, "Mocked fixed public error", status)
    operation = str(uuid.uuid4())
    for _ in range(2):
        with pytest.raises(AppError) as error:
            service.run(file_bytes=image_bytes, operation_id=operation)
        assert error.value.code == code
    assert len(client.calls) == 1 and quota.snapshot()["month"]["used"] == 1
    assert db.history() == []
