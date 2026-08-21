"""
Deduplicate Spotify Playlist & Clean Links File
Removes duplicate tracks from the Spotify playlist and cleans up spotify_links.txt.
"""

import os
import sys
from dotenv import load_dotenv
from spotify_client import SpotifySyncClient
from state_db import StateDB

load_dotenv()

def clean_playlist():
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    playlist_id = os.getenv("SPOTIFY_PLAYLIST_ID")
    links_file = os.getenv("LINKS_FILE_PATH", "spotify_links.txt")
    db_file = os.getenv("DB_FILE_PATH", "sync_history.db")

    print("==================================================")
    print("  Spotify Playlist Deduplication & Cleanup")
    print("==================================================")

    spotify = SpotifySyncClient(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        playlist_id=playlist_id
    )
    db = StateDB(db_file)

    # 1. Fetch all track IDs from Spotify
    all_tracks = spotify.get_playlist_track_ids()
    print(f"Current total tracks in Spotify playlist: {len(all_tracks)}")

    unique_track_ids = []
    seen = set()
    duplicates_count = 0

    for tid in all_tracks:
        if tid not in seen:
            seen.add(tid)
            unique_track_ids.append(tid)
        else:
            duplicates_count += 1

    print(f"Unique tracks: {len(unique_track_ids)} | Duplicate entries found: {duplicates_count}")

    if duplicates_count > 0:
        print("Cleaning up Spotify playlist duplicates...")
        # Replace playlist contents with the unique tracks in chunks of 100
        uris = [f"spotify:track:{tid}" for tid in unique_track_ids]
        
        # First 100 tracks replace playlist
        first_chunk = uris[:100]
        spotify.sp.playlist_replace_items(playlist_id, first_chunk)
        
        # Remaining tracks added in chunks
        for i in range(100, len(uris), 100):
            chunk = uris[i:i+100]
            spotify.sp.playlist_add_items(playlist_id, chunk)

        print(f"✅ Spotify playlist successfully cleaned! Now contains {len(unique_track_ids)} unique tracks.")
    else:
        print("✅ No duplicates found in Spotify playlist.")

    # 2. Clean spotify_links.txt to ensure only unique links exist
    if os.path.exists(links_file):
        with open(links_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        cleaned_lines = []
        file_seen = set()
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            # extract track ID
            import re
            m = re.search(r'track/([a-zA-Z0-9]{15,30})|spotify:track:([a-zA-Z0-9]{15,30})', line_str)
            if m:
                tid = m.group(1) or m.group(2)
                if tid not in file_seen:
                    file_seen.add(tid)
                    cleaned_lines.append(f"https://open.spotify.com/track/{tid}\n")
            else:
                if line_str not in file_seen:
                    file_seen.add(line_str)
                    cleaned_lines.append(f"{line_str}\n")

        with open(links_file, "w", encoding="utf-8") as f:
            f.writelines(cleaned_lines)
        print(f"✅ Cleaned {links_file}: {len(cleaned_lines)} unique links written.")

    # 3. Synchronize database
    print("Re-indexing database...")
    batch = []
    for tid in unique_track_ids:
        info = spotify.get_track_info(tid)
        batch.append({
            "track_id": tid,
            "title": info.get("title", ""),
            "artist": info.get("artist", ""),
            "album": info.get("album", ""),
            "artwork_url": info.get("artwork_url", ""),
            "source": "dedup_cleanup"
        })
    if batch:
        db.record_tracks_batch(batch)
    print("✅ Database synchronized.")

if __name__ == "__main__":
    clean_playlist()
