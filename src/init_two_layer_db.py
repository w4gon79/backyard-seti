"""Add two_layer_jobs table to the SETI database."""
import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'seti_hits.db')
conn = sqlite3.connect(DB)
c = conn.cursor()

c.execute("""
    CREATE TABLE IF NOT EXISTS two_layer_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT UNIQUE NOT NULL,
        target TEXT NOT NULL,
        tolerance_hz REAL,
        min_epochs INTEGER,
        min_snr REAL,
        stack_width REAL,
        n_sigma REAL,
        status TEXT DEFAULT 'running',
        progress INTEGER DEFAULT 0,
        progress_msg TEXT,
        n_candidates INTEGER DEFAULT 0,
        n_stacked INTEGER DEFAULT 0,
        n_with_peaks INTEGER DEFAULT 0,
        verdict TEXT,
        result_json TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT
    )
""")
conn.commit()
print("two_layer_jobs table created (or already exists)")
conn.close()
