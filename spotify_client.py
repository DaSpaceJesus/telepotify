"""
Spotify API Client
Handles headless OAuth authentication, playlist inspection, and track management.
"""

import os
import re
import spotipy
from spotipy.oauth2 import SpotifyOAuth, CacheFileHandler
from typing import List, Dict, Optional, Tuple

class SpotifySyncClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        playlist_id: str,
        cache_path: str = ".cache-spotify"
    ):
        self.playlist_id = str(playlist_id).strip()
        self.cache_path = cache_path
        scope = "playlist-read-private playlist-read-collaborative playlist-modify-public playlist-modify-private"
        
        cache_handler = CacheFileHandler(cache_path=cache_path)
        self.auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope,
            cache_handler=cache_handler,
            open_browser=False  # Headless mode for Ubuntu Server
        )
        self.sp = spotipy.Spotify(
            auth_manager=self.auth_manager,
            requests_timeout=25,
            retries=5,
            status_retries=5,
            backoff_factor=0.5
        )
        self._id_re = re.compile(r'^[a-zA-Z0-9]{15,30}$')
        self._secure_cache_file()

    def _secure_cache_file(self):
        """Enforces 0600 permissions on Spotify OAuth token cache file."""
        if os.path.exists(self.cache_path):
            try:
                os.chmod(self.cache_path, 0o600)
            except OSError as e:
                print(f"[SpotifyClient] Warning: Could not set 0600 on {self.cache_path}: {e}")

    def _is_valid_id(self, entity_id: str) -> bool:
        return bool(entity_id and self._id_re.match(str(entity_id).strip()))

    def get_playlist_track_ids(self) -> List[str]:
        """Fetches all track IDs currently in the target Spotify playlist (handling pagination and new/legacy API keys)."""
        track_ids = []
        try:
            results = self.sp.playlist_items(self.playlist_id, limit=100)
            while results:
                for entry in results.get("items", []):
                    if not entry:
                        continue
                    track_obj = entry.get("item") or entry.get("track")
                    if track_obj and isinstance(track_obj, dict) and track_obj.get("id"):
                        track_ids.append(track_obj["id"])
                if results.get("next"):
                    results = self.sp.next(results)
                else:
                    break
        except Exception as e:
            print(f"[SpotifyClient] Error fetching playlist track IDs: {e}")
        return track_ids

    def get_playlist_total(self) -> int:
        """Returns the total number of tracks currently in the target playlist."""
        try:
            pl = self.sp.playlist(self.playlist_id, fields="tracks.total")
            return pl.get("tracks", {}).get("total", 0)
        except Exception:
            return len(self.get_playlist_track_ids())

    def get_track_info(self, track_id: str) -> Dict:
        """Fetches metadata for a single Spotify track."""
        try:
            t = self.sp.track(track_id)
            title = t.get("name", "Unknown Title")
            artists = ", ".join([a["name"] for a in t.get("artists", [])])
            album = t.get("album", {}).get("name", "")
            
            # Extract highest resolution album artwork
            images = t.get("album", {}).get("images", [])
            artwork_url = images[0]["url"] if images else ""
            
            return {
                "track_id": track_id,
                "title": title,
                "artist": artists or "Unknown Artist",
                "album": album,
                "artwork_url": artwork_url,
                "url": f"https://open.spotify.com/track/{track_id}"
            }
        except Exception as e:
            return {
                "track_id": track_id,
                "title": "Unknown Track",
                "artist": "Unknown Artist",
                "album": "",
                "artwork_url": "",
                "url": f"https://open.spotify.com/track/{track_id}"
            }

    def add_tracks(self, track_ids: List[str], check_existing: bool = True, max_retries: int = 3) -> bool:
        """Adds a list of track IDs to the target playlist in batches of 100 with strict deduplication."""
        valid_ids = [tid for tid in track_ids if self._is_valid_id(tid)]
        if not valid_ids:
            return True

        if check_existing:
            existing_ids = set(self.get_playlist_track_ids())
            valid_ids = [tid for tid in valid_ids if tid not in existing_ids]
            if not valid_ids:
                return True

        uris = [f"spotify:track:{tid}" for tid in valid_ids]
        for attempt in range(1, max_retries + 1):
            try:
                for i in range(0, len(uris), 100):
                    chunk = uris[i:i+100]
                    self.sp.playlist_add_items(self.playlist_id, chunk)
                return True
            except Exception as e:
                print(f"[SpotifyClient] Attempt {attempt}/{max_retries} failed to add tracks: {e}")
                if attempt < max_retries:
                    import time
                    time.sleep(1.0 * attempt)
        return False

    def remove_tracks(self, track_ids: List[str], max_retries: int = 3) -> bool:
        """Removes a list of track IDs from the target playlist with automatic retry."""
        valid_ids = [tid for tid in track_ids if self._is_valid_id(tid)]
        if not valid_ids:
            return True
        uris = [f"spotify:track:{tid}" for tid in valid_ids]
        for attempt in range(1, max_retries + 1):
            try:
                for i in range(0, len(uris), 100):
                    chunk = uris[i:i+100]
                    self.sp.playlist_remove_all_occurrences_of_items(self.playlist_id, chunk)
                return True
            except Exception as e:
                print(f"[SpotifyClient] Attempt {attempt}/{max_retries} failed to remove tracks: {e}")
                if attempt < max_retries:
                    import time
                    time.sleep(1.0 * attempt)
        return False

    def get_album_info(self, album_id: str) -> Dict:
        """Fetches metadata and all track IDs for a Spotify album."""
        try:
            album = self.sp.album(album_id)
            album_name = album.get("name", "Unknown Album")
            artist = ", ".join([a["name"] for a in album.get("artists", [])])
            images = album.get("images", [])
            artwork_url = images[0]["url"] if images else ""
            
            # Fetch all tracks in the album
            track_ids = []
            tracks = album.get("tracks", {})
            while tracks:
                for item in tracks.get("items", []):
                    if item.get("id"):
                        track_ids.append(item["id"])
                if tracks.get("next"):
                    tracks = self.sp.next(tracks)
                else:
                    break
                    
            return {
                "entity_id": album_id,
                "type": "album",
                "title": album_name,
                "artist": artist,
                "artwork_url": artwork_url,
                "track_count": len(track_ids),
                "track_ids": track_ids
            }
        except Exception as e:
            print(f"[SpotifyClient] Error fetching album {album_id}: {e}")
            return {}

    def get_source_playlist_info(self, playlist_id: str) -> Dict:
        """Fetches metadata and all track IDs from an external Spotify playlist."""
        try:
            pl = self.sp.playlist(playlist_id)
            pl_name = pl.get("name", "Unknown Playlist")
            owner = pl.get("owner", {}).get("display_name", "Spotify User")
            images = pl.get("images", [])
            artwork_url = images[0]["url"] if images else ""
            
            track_ids = []
            tracks = pl.get("tracks", {})
            while tracks:
                for item in tracks.get("items", []):
                    t = item.get("track")
                    if t and t.get("id"):
                        track_ids.append(t["id"])
                if tracks.get("next"):
                    tracks = self.sp.next(tracks)
                else:
                    break
                    
            return {
                "entity_id": playlist_id,
                "type": "playlist",
                "title": pl_name,
                "artist": f"Curated by {owner}",
                "artwork_url": artwork_url,
                "track_count": len(track_ids),
                "track_ids": track_ids
            }
        except Exception as e:
            print(f"[SpotifyClient] Error fetching playlist {playlist_id}: {e}")
            return {}
