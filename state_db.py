"""
State Database Manager (SQLite)
Tracks synced tracks, deduplication state, and pending approval prompts.
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple

class StateDB:
    def __init__(self, db_path: str = "sync_history.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Synced tracks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    track_id TEXT PRIMARY KEY,
                    title TEXT,
                    artist TEXT,
                    album TEXT,
                    artwork_url TEXT,
                    added_at TIMESTAMP,
                    source TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
            # Pending approvals for albums/playlists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    approval_id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    entity_id TEXT,
                    title TEXT,
                    artist TEXT,
                    track_count INTEGER,
                    track_ids_json TEXT,
                    message_ids_json TEXT,
                    created_at TIMESTAMP,
                    status TEXT DEFAULT 'pending',
                    resolved_by TEXT
                )
            """)
            conn.commit()

    # --- Tracks Operations ---

    def get_all_active_track_ids(self) -> List[str]:
        """Returns all track IDs currently marked as active in the playlist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT track_id FROM tracks WHERE is_active = 1")
            return [row["track_id"] for row in cursor.fetchall()]

    def is_track_synced(self, track_id: str) -> bool:
        """Checks if a track ID has already been recorded as active."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_active FROM tracks WHERE track_id = ?", (track_id,))
            row = cursor.fetchone()
            return row is not None and row["is_active"] == 1

    def get_track_info(self, track_id: str) -> Optional[Dict]:
        """Retrieves stored metadata for a track."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tracks WHERE track_id = ?", (track_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def record_track_added(
        self,
        track_id: str,
        title: str,
        artist: str,
        album: str = "",
        artwork_url: str = "",
        source: str = "telegram"
    ):
        """Records a new track addition or marks an existing track as active."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute("""
                INSERT INTO tracks (track_id, title, artist, album, artwork_url, added_at, source, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(track_id) DO UPDATE SET
                    is_active = 1,
                    title = COALESCE(excluded.title, tracks.title),
                    artist = COALESCE(excluded.artist, tracks.artist),
                    album = COALESCE(excluded.album, tracks.album),
                    artwork_url = COALESCE(excluded.artwork_url, tracks.artwork_url)
            """, (track_id, title, artist, album, artwork_url, now, source))
            conn.commit()

    def record_tracks_batch(self, tracks_data: List[Dict]):
        """Batch insert/update tracks."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            for t in tracks_data:
                cursor.execute("""
                    INSERT INTO tracks (track_id, title, artist, album, artwork_url, added_at, source, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(track_id) DO UPDATE SET is_active = 1
                """, (
                    t["track_id"],
                    t.get("title", "Unknown Title"),
                    t.get("artist", "Unknown Artist"),
                    t.get("album", ""),
                    t.get("artwork_url", ""),
                    now,
                    t.get("source", "initial_sync")
                ))
            conn.commit()

    def record_track_removed(self, track_id: str):
        """Marks a track as inactive when removed from playlist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tracks SET is_active = 0 WHERE track_id = ?", (track_id,))
            conn.commit()

    def get_total_active_count(self) -> int:
        """Returns total active tracks count in DB."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM tracks WHERE is_active = 1")
            row = cursor.fetchone()
            return row["count"] if row else 0

    # --- Approval Prompts Operations ---

    def create_pending_approval(
        self,
        approval_id: str,
        entity_type: str,
        entity_id: str,
        title: str,
        artist: str,
        track_count: int,
        track_ids: List[str],
        message_ids: Dict[str, int]
    ):
        """Saves a pending confirmation prompt for an album or playlist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute("""
                INSERT INTO pending_approvals 
                (approval_id, entity_type, entity_id, title, artist, track_count, track_ids_json, message_ids_json, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                approval_id,
                entity_type,
                entity_id,
                title,
                artist,
                track_count,
                json.dumps(track_ids),
                json.dumps(message_ids),
                now
            ))
            conn.commit()

    def get_pending_approval(self, approval_id: str) -> Optional[Dict]:
        """Retrieves details of a pending approval prompt."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM pending_approvals WHERE approval_id = ?", (approval_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["track_ids"] = json.loads(d["track_ids_json"])
                d["message_ids"] = json.loads(d["message_ids_json"])
                return d
            return None

    def resolve_approval(self, approval_id: str, status: str, resolved_by: str) -> bool:
        """Resolves an approval (status: 'approved' or 'dismissed'). Returns True if updated."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE pending_approvals 
                SET status = ?, resolved_by = ?
                WHERE approval_id = ? AND status = 'pending'
            """, (status, resolved_by, approval_id))
            conn.commit()
            return cursor.rowcount > 0
