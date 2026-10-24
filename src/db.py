"""SQLite schema and setup. Every bucket must trace back to rows in signal_observations."""
import sqlite3
from pathlib import Path

from src.config import Config
from src.match.normalize import normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id               INTEGER PRIMARY KEY,
    raw_name         TEXT NOT NULL UNIQUE,
    normalized_name  TEXT NOT NULL,
    cik              TEXT UNIQUE,
    main_domain      TEXT,
    github_org       TEXT,
    first_check_date TEXT,              -- when the fund invested; Form D after this = raised again
    legal_name       TEXT,              -- from the site's copyright line; what Form D is filed under
    city             TEXT,
    sector           TEXT,
    description      TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_entities_norm ON entities(normalized_name);

CREATE TABLE IF NOT EXISTS signal_observations (
    id          INTEGER PRIMARY KEY,
    entity_id   INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    signal_type TEXT NOT NULL CHECK (signal_type IN ('form_d', 'engineering', 'hiring', 'footprint', 'domain')),
    observed_at TEXT NOT NULL DEFAULT (datetime('now')),
    event_date  TEXT,                   -- when the thing happened (filing date, last push), not when we saw it
    source_url  TEXT,
    payload     TEXT NOT NULL CHECK (json_valid(payload)),
    score       REAL CHECK (score BETWEEN 0.0 AND 1.0),
    raw_path    TEXT                    -- the dump in data/raw this row was parsed from
);
CREATE INDEX IF NOT EXISTS idx_obs_entity ON signal_observations(entity_id, signal_type, observed_at);

CREATE TABLE IF NOT EXISTS match_candidates (
    id             INTEGER PRIMARY KEY,
    entity_id      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source         TEXT NOT NULL,       -- 'sec_edgar', 'github', ...
    candidate_name TEXT NOT NULL,
    candidate_key  TEXT NOT NULL,       -- CIK, GitHub login, domain
    jaro_winkler   REAL,
    token_set      REAL,
    domain_match   INTEGER NOT NULL DEFAULT 0 CHECK (domain_match IN (0, 1)),
    score          REAL NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
    state          TEXT NOT NULL CHECK (state IN ('AUTO_LINKED', 'PENDING_REVIEW', 'CONFIRMED', 'REJECTED')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at    TEXT,
    UNIQUE (entity_id, source, candidate_key)
);
CREATE INDEX IF NOT EXISTS idx_candidates_state ON match_candidates(state);

CREATE TABLE IF NOT EXISTS portfolio_daily_scores (
    id                 INTEGER PRIMARY KEY,
    entity_id          INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    score_date         TEXT NOT NULL,
    form_d_score       REAL,
    engineering_score  REAL,
    hiring_score       REAL,
    footprint_score    REAL,
    composite_score    REAL CHECK (composite_score BETWEEN 0.0 AND 1.0),
    -- 'Unresolved' = no verified domain yet; we don't classify what we can't find
    bucket             TEXT NOT NULL CHECK (bucket IN ('Raised Again', 'Active Footprint', 'Quiet Phase', 'No Public Activity', 'Unresolved')),
    last_activity_date TEXT,            -- rendered as "No Public Activity Since <date>"
    evidence           TEXT NOT NULL CHECK (json_valid(evidence)),  -- signal_observations ids and thresholds used
    UNIQUE (entity_id, score_date)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(cfg: Config) -> sqlite3.Connection:
    """Create the tables and load the portfolio from config. Safe to re-run."""
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(cfg.db_path)
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entities)")}
    if "legal_name" not in cols:  # databases created before the column existed
        conn.execute("ALTER TABLE entities ADD COLUMN legal_name TEXT")
    with conn:
        for t in cfg.portfolio:
            conn.execute(
                """INSERT INTO entities (raw_name, normalized_name, cik, main_domain, github_org, first_check_date)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(raw_name) DO UPDATE SET
                     normalized_name = excluded.normalized_name,
                     cik = COALESCE(excluded.cik, entities.cik),
                     main_domain = COALESCE(excluded.main_domain, entities.main_domain),
                     github_org = COALESCE(excluded.github_org, entities.github_org),
                     first_check_date = COALESCE(excluded.first_check_date, entities.first_check_date)""",
                (t.name, normalize(t.name), t.cik, t.domain, t.github_org, t.first_check_date),
            )
    return conn
