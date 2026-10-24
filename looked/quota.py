import uuid
from datetime import datetime, timedelta, timezone

from .errors import AppError


class QuotaManager:
    def __init__(self, database, settings, now=None):
        self.db = database
        self.settings = settings
        self.now = now or (lambda: datetime.now(timezone.utc))

    def periods(self):
        now = self.now().astimezone(timezone.utc)
        next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return [
            ("month", now.strftime("%Y-%m"), self.settings.monthly_limit, next_month.isoformat()),
            ("day", now.strftime("%Y-%m-%d"), self.settings.daily_limit,
             (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()),
            ("hour", now.strftime("%Y-%m-%dT%H"), self.settings.hourly_limit,
             (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).isoformat()),
        ]

    def snapshot(self):
        periods = {}
        with self.db.connect() as conn:
            for period, bucket, limit, reset in self.periods():
                row = conn.execute("SELECT * FROM usage WHERE period=? AND bucket=?", (period, bucket)).fetchone()
                used = row["searches"] if row else 0
                periods[period] = {"used": used, "limit": limit, "remaining": max(0, limit-used),
                                   "requests": row["requests"] if row else 0, "resets_at": reset}
        return periods

    def reserve(self):
        attempt = uuid.uuid4().hex
        with self.db.connect(write=True) as conn:
            periods = self.periods()
            for period, bucket, limit, reset in periods:
                row = conn.execute("SELECT searches FROM usage WHERE period=? AND bucket=?", (period, bucket)).fetchone()
                if (row[0] if row else 0) >= limit:
                    raise AppError("local_quota", f"The local {period} search limit has been reached. Resets at {reset}. Cached searches still work.", 429)
            for period, bucket, _, _ in periods:
                conn.execute("""INSERT INTO usage (period, bucket, searches) VALUES (?, ?, 1)
                    ON CONFLICT(period, bucket) DO UPDATE SET searches=searches+1""", (period, bucket))
            conn.execute("INSERT INTO attempts (id, created_at) VALUES (?, ?)", (attempt, self.now().isoformat()))
        return attempt

    def record_http_request(self):
        with self.db.connect(write=True) as conn:
            for period, bucket, _, _ in self.periods():
                conn.execute("""INSERT INTO usage (period, bucket, requests) VALUES (?, ?, 1)
                    ON CONFLICT(period, bucket) DO UPDATE SET requests=requests+1""", (period, bucket))

    def finish(self, attempt, outcome):
        with self.db.connect(write=True) as conn:
            conn.execute("UPDATE attempts SET outcome=? WHERE id=?", (outcome, attempt))
