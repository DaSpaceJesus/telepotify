"""
Spotify API Client
Handles headless OAuth authentication, playlist inspection, and track management.
"""

import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
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
        self.playlist_id = playlist_id
        scope = "playlist-read-private playlist-read-collaborative playlist-modify-public playlist-modify-private"
        
        self.auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope,
            cache_path=cache_path,
            open_browser=False  # Headless mode for Ubuntu Server
        )
        self.sp = spotipy.Spotify(auth_manager=self.auth_manager)

    def get_playlist_track_ids(self) -> List[str]:
        """Fetches all track IDs currently in the target Spotify playlist (handling pagination)."""
        track_ids = []
        results = self.sp.playlist_items(self.playlist_id, fields="items.track.id,next", limit=100)
        
        while results:
            for item in results.get("items", []):
                track = item.get("track")
                if track and track.get("id"):
                    track_ids.append(track["id"])
            if results.get("next"):
                results = self.sp.next(results)
            else:
                break
                
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

    def add_tracks(self, track_ids: List[str]) -> bool:
        """Adds a list of track IDs to the target playlist in batches of 100."""
        if not track_ids:
            return True
        try:
            uris = [f"spotify:track:{tid}" for tid in track_ids]
            # Batch in chunks of 100 (Spotify API limit)
            for i in range(0, len(uris), 100):
                chunk = uris[i:i+100]
                self.sp.playlist_add_items(self.playlist_id, chunk)
            return True
        except Exception as e:
            print(f"[SpotifyClient] Error adding tracks: {e}")
            return False

    def remove_tracks(self, track_ids: List[str]) -> bool:
        """Removes a list of track IDs from the target playlist."""
        if not track_ids:
            return True
        try:
            uris = [f"spotify:track:{tid}" for tid in track_ids]
            for i in range(0, len(uris), 100):
                chunk = uris[i:i+100]
                self.sp.playlist_remove_all_occurrences_of_items(self.playlist_id, chunk)
            return True
        except Exception as e:
            print(f"[SpotifyClient] Error removing tracks: {e}")
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
