from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    root: Path
    api_key: str = field(default="", repr=False)
    monthly_limit: int = 200
    daily_limit: int = 20
    hourly_limit: int = 40
    port: int = 5000

    @property
    def db_path(self):
        return self.root / "data" / "looked.sqlite3"

    @property
    def key_configured(self):
        return bool(self.api_key and self.api_key != "your_serpapi_api_key_here")

    @classmethod
    def load(cls, root):
        # Deliberately do not put the key in os.environ or frontend configuration.
        values = dotenv_values(Path(root) / ".env", interpolate=False)

        def integer(name, default, maximum):
            try:
                value = int(values.get(name, str(default)))
                if not 0 <= value <= maximum:
                    raise ValueError
                return value
            except (TypeError, ValueError):
                raise ValueError(f"Check the numeric setting {name} in .env.") from None

        port = integer("PORT", 5000, 65535)
        if port < 1024:
            raise ValueError("PORT must be between 1024 and 65535.")
        return cls(
            root=Path(root), api_key=(values.get("SERPAPI_API_KEY") or "").strip(),
            monthly_limit=integer("MONTHLY_SEARCH_LIMIT", 200, 1_000_000),
            daily_limit=integer("DAILY_SEARCH_LIMIT", 20, 1_000_000),
            hourly_limit=integer("HOURLY_SEARCH_LIMIT", 40, 1_000_000), port=port,
        )
