"""
Source File Watcher & 2-Way Sync Engine
Monitors spotify_links.txt and performs bidirectional synchronization with Spotify playlist.
"""

import os
import re
import time
import asyncio
from typing import List, Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class FileSyncHandler(FileSystemEventHandler):
    def __init__(self, target_file: str, loop: asyncio.AbstractEventLoop, on_file_changed_callback):
        super().__init__()
        self.target_file = os.path.abspath(target_file)
        self.loop = loop
        self.callback = on_file_changed_callback
        self.last_modified = 0

    def on_modified(self, event):
        if os.path.abspath(event.src_path) == self.target_file:
            current_time = time.time()
            # Debounce rapid file writes (1.0s window)
            if current_time - self.last_modified > 1.0:
                self.last_modified = current_time
                asyncio.run_coroutine_threadsafe(self.callback(), self.loop)


class PlaylistFileWatcher:
    def __init__(
        self,
        links_file_path: str,
        spotify_client,
        state_db,
        telegram_reporter,
        loop: asyncio.AbstractEventLoop
    ):
        self.links_file_path = os.path.abspath(links_file_path)
        self.spotify_client = spotify_client
        self.state_db = state_db
        self.telegram_reporter = telegram_reporter
        self.loop = loop
        self.observer = None
        self._is_syncing = False
        self._track_id_re = re.compile(r'track/([a-zA-Z0-9]+)|spotify:track:([a-zA-Z0-9]+)')

        # Ensure links file exists
        if not os.path.exists(self.links_file_path):
            with open(self.links_file_path, 'w', encoding='utf-8') as f:
                pass

    def start(self):
        """Starts the watchdog file monitoring thread."""
        event_handler = FileSyncHandler(self.links_file_path, self.loop, self.sync_diff)
        self.observer = Observer()
        dir_path = os.path.dirname(self.links_file_path) or "."
        self.observer.schedule(event_handler, path=dir_path, recursive=False)
        self.observer.start()
        print(f"[FileWatcher] Watching {self.links_file_path} for changes...")

    def stop(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()

    def extract_track_ids_from_file(self) -> List[str]:
        """Parses spotify_links.txt and returns ordered unique track IDs."""
        if not os.path.exists(self.links_file_path):
            return []
        
        track_ids = []
        seen = set()
        with open(self.links_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = self._track_id_re.search(line)
                if m:
                    tid = m.group(1) or m.group(2)
                    if tid and tid not in seen:
                        seen.add(tid)
                        track_ids.append(tid)
        return track_ids

    def append_links(self, urls: List[str]):
        """Appends new Spotify links to the source file."""
        with open(self.links_file_path, 'a', encoding='utf-8') as f:
            for u in urls:
                f.write(f"{u.strip()}\n")

    async def sync_diff(self):
        """Calculates differences between spotify_links.txt and Spotify playlist and syncs."""
        if self._is_syncing:
            return
        self._is_syncing = True

        try:
            file_track_ids = self.extract_track_ids_from_file()
            db_active_ids = set(self.state_db.get_all_active_track_ids())
            file_ids_set = set(file_track_ids)

            # Determine tracks to add (in file, but not active in DB/Spotify)
            to_add = [tid for tid in file_track_ids if tid not in db_active_ids]
            
            # Determine tracks to remove (active in DB/Spotify, but removed from file)
            to_remove = [tid for tid in db_active_ids if tid not in file_ids_set]

            # 1. Process Additions
            if to_add:
                print(f"[FileWatcher] Found {len(to_add)} new track(s) in file to add...")
                for tid in to_add:
                    track_info = self.spotify_client.get_track_info(tid)
                    success = self.spotify_client.add_tracks([tid])
                    if success:
                        self.state_db.record_track_added(
                            track_id=tid,
                            title=track_info.get("title", ""),
                            artist=track_info.get("artist", ""),
                            album=track_info.get("album", ""),
                            artwork_url=track_info.get("artwork_url", ""),
                            source="file_watcher"
                        )
                        total = self.spotify_client.get_playlist_total()
                        print(f"[FileWatcher] Added: {track_info.get('artist')} - {track_info.get('title')}")
                        await self.telegram_reporter.send_track_added(track_info, total)

            # 2. Process Removals (2-Way Deletion)
            if to_remove:
                print(f"[FileWatcher] Found {len(to_remove)} track(s) removed from file. Removing from Spotify...")
                for tid in to_remove:
                    track_info = self.state_db.get_track_info(tid) or self.spotify_client.get_track_info(tid)
                    success = self.spotify_client.remove_tracks([tid])
                    if success:
                        self.state_db.record_track_removed(tid)
                        total = self.spotify_client.get_playlist_total()
                        print(f"[FileWatcher] Removed: {track_info.get('artist')} - {track_info.get('title')}")
                        await self.telegram_reporter.send_track_removed(track_info, total)

        except Exception as e:
            print(f"[FileWatcher] Error during sync diff: {e}")
        finally:
            self._is_syncing = False
