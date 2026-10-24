"""Load and check config/census.yaml. Bad settings fail here, not mid-sync."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "census.yaml"
SIGNALS = ("form_d", "engineering", "hiring", "footprint")


class ConfigError(ValueError):
    pass


def load_env(path: Path = ROOT / ".env") -> None:
    """KEY=value lines from census/.env (gitignored). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Target:
    name: str
    domain: str | None = None
    github_org: str | None = None
    cik: str | None = None
    first_check_date: str | None = None


@dataclass(frozen=True)
class Config:
    fund_name: str
    sec_user_agent: str
    db_path: Path
    raw_dir: Path
    window_days: int
    probe_timeout: float
    probe_concurrency: int
    auto_link: float
    reject: float
    weights: dict[str, float]
    portfolio: list[Target] = field(default_factory=list)


def _require(section: dict, key: str, where: str):
    if key not in section:
        raise ConfigError(f"missing '{key}' in {where}")
    return section[key]


def load(path: Path = DEFAULT_CONFIG) -> Config:
    load_env()
    raw = yaml.safe_load(path.read_text()) or {}
    fund = _require(raw, "fund", path.name)
    paths = _require(raw, "paths", path.name)
    probe = _require(raw, "probe", path.name)
    match = _require(raw, "match", path.name)
    weights = _require(raw, "weights", path.name)

    user_agent = os.environ.get("SEC_USER_AGENT") or _require(fund, "sec_user_agent", "fund")
    if "@" not in user_agent:
        raise ConfigError("sec_user_agent needs a contact email (SEC fair-access rules)")

    if set(weights) != set(SIGNALS):
        raise ConfigError(f"weights must be exactly {', '.join(SIGNALS)}")
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ConfigError(f"weights sum to {sum(weights.values()):.3f}, not 1.0")

    auto_link, reject = float(match["auto_link"]), float(match["reject"])
    if not 0 < reject < auto_link <= 1:
        raise ConfigError("need 0 < match.reject < match.auto_link <= 1")

    portfolio = []
    for i, row in enumerate(raw.get("portfolio") or []):
        if not row.get("name"):
            raise ConfigError(f"portfolio row {i + 1} has no name")
        portfolio.append(Target(**{k: (str(v) if v is not None else None) for k, v in row.items()}))

    return Config(
        fund_name=_require(fund, "name", "fund"),
        sec_user_agent=user_agent,
        db_path=ROOT / _require(paths, "db", "paths"),
        raw_dir=ROOT / _require(paths, "raw", "paths"),
        window_days=int(raw.get("window_days", 30)),
        probe_timeout=float(probe.get("timeout_seconds", 5)),
        probe_concurrency=int(probe.get("concurrency", 20)),
        auto_link=auto_link,
        reject=reject,
        weights={k: float(v) for k, v in weights.items()},
        portfolio=portfolio,
    )
