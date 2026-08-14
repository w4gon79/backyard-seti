#!/usr/bin/env python3
"""Backfill rfi_zoned flags on existing hits in the DB.

Flags any hit whose (source_file epoch, freq) falls inside a zone from
data/rfi_zones.json. Safe to re-run (idempotent UPDATE). Adds the
rfi_zoned column if the DB predates the migration.

Usage: python tmp_backfill_zoned.py   (from G:\\seti)
"""
import os
import sys
import json
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rfi_zones

DB = os.path.join('data', 'seti_hits.db')

zones = rfi_zones._load()
if not zones:
    print('no zones configured, nothing to do')
    sys.exit(0)

conn = sqlite3.connect(DB)
cols = [r[1] for r in conn.execute('PRAGMA table_info(hits)').fetchall()]
if 'rfi_zoned' not in cols:
    conn.execute('ALTER TABLE hits ADD COLUMN rfi_zoned INTEGER DEFAULT 0')
    conn.commit()
    print('added rfi_zoned column')

total = 0
for epoch, zlist in zones.items():
    for z in zlist:
        cur = conn.execute(
            "UPDATE hits SET rfi_zoned = 1 "
            "WHERE source_file LIKE ? AND freq BETWEEN ? AND ? "
            "AND (rfi_zoned IS NULL OR rfi_zoned = 0)",
            (f'%_{epoch}_%', z['f_start'], z['f_stop']))
        print(f"{epoch} {z['f_start']}-{z['f_stop']}: flagged {cur.rowcount} hits")
        total += cur.rowcount
conn.commit()

n = conn.execute('SELECT COUNT(*) FROM hits WHERE rfi_zoned = 1').fetchone()[0]
conn.close()
print(f"total newly flagged: {total}; zoned hits in DB now: {n}")
