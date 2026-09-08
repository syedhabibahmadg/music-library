"""
Local SQLite storage for the music library.

Everything lives in one table, `songs`. The `path` column is UNIQUE, and
every insert uses INSERT OR IGNORE. That one detail gives us two things
at once:
    - Re-scanning a folder never creates duplicate rows (same path =
      already there, so the insert is skipped).
    - Re-scanning never overwrites tag fields (genre, bpm, style, mood,
      sounds_like, favorite) that a later chunk will let the user edit -
      because a row that already exists is left completely untouched.

The database file's location depends on how the app is running - see
_get_app_dir() below for the full explanation (source run vs. a packaged
Windows .exe vs. a packaged macOS .app all differ).
"""

import sys
import sqlite3
from pathlib import Path


def _get_app_dir() -> Path:
    """
    Return the folder the app's data should live in.

    Running from source, that's simply the project folder (next to
    main.py). `sys.frozen` is set by PyInstaller specifically so code
    can detect a packaged build; the two packaged platforms need
    different handling:

    - Windows (.exe): the database sits next to the .exe itself
      (`sys.executable`'s folder), keeping the whole app portable in
      one folder - the way portable Windows apps normally work.

    - macOS (.app): PyInstaller's `sys.executable` for a macOS bundle
      points *inside* the bundle, at
      `MusicLibrary.app/Contents/MacOS/MusicLibrary`. Writing the
      database there would put user data inside the app bundle itself,
      which macOS treats as a read-only package - it can invalidate
      code signing/notarization ("app is damaged" from Gatekeeper),
      and a bundle installed in /Applications usually isn't writable
      by a normal user at all. macOS apps are expected to store their
      data in the user's Application Support folder instead, so that's
      what's used here.
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            app_support_dir = Path.home() / "Library" / "Application Support" / "Music Library"
            app_support_dir.mkdir(parents=True, exist_ok=True)
            return app_support_dir
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


DB_PATH = _get_app_dir() / "music_library.db"


def get_connection() -> sqlite3.Connection:
    """Open a connection, make sure the schema exists, and migrate old data."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            title TEXT,
            artist TEXT,
            album TEXT,
            duration REAL,
            genre TEXT,
            bpm REAL,
            style TEXT,
            mood TEXT,
            sounds_like TEXT,
            is_favorite INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    _migrate_old_tracks_table(conn)
    return conn


def _migrate_old_tracks_table(conn) -> None:
    """
    One-time migration: Chunk 2 stored songs in a table named `tracks`.
    If that table still exists and `songs` is empty, copy the old rows
    over so anything already scanned isn't lost. Safe to call every time
    the app starts - it only does anything once.
    """
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tracks'"
    ).fetchone()
    if table_exists is None:
        return

    song_count = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    if song_count > 0:
        return

    old_rows = conn.execute(
        "SELECT path, filename, title, artist, album, duration FROM tracks"
    ).fetchall()
    for path, filename, title, artist, album, duration in old_rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO songs (path, filename, title, artist, album, duration)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (path, filename, title, artist, album, duration),
        )
    conn.commit()


def add_song(conn, path, filename, title, artist, album, duration) -> None:
    """
    Insert a new song row. If `path` already exists, this does nothing.
    That's what prevents duplicates on rescan and protects any tag
    fields (genre/bpm/style/mood/sounds_like/favorite) already set on
    the existing row.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO songs (path, filename, title, artist, album, duration)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (path, filename, title, artist, album, duration),
    )


def get_song_tags(conn, path):
    """
    Return (genre, bpm, style, mood, sounds_like) for the song at `path`,
    or None if no song with that path exists.
    """
    return conn.execute(
        "SELECT genre, bpm, style, mood, sounds_like FROM songs WHERE path = ?",
        (path,),
    ).fetchone()


def update_song_tags(conn, path, genre, bpm, style, mood, sounds_like) -> None:
    """
    Update only the tag fields for an existing song, identified by path.
    Does not touch path/filename/title/artist/album/duration - those stay
    exactly as the scanner last set them.
    """
    conn.execute(
        """
        UPDATE songs
        SET genre = ?, bpm = ?, style = ?, mood = ?, sounds_like = ?
        WHERE path = ?
        """,
        (genre, bpm, style, mood, sounds_like, path),
    )
    conn.commit()


def set_favorite(conn, path, is_favorite: bool) -> None:
    """Set (or clear) the favorite flag for a song, identified by path."""
    conn.execute(
        "UPDATE songs SET is_favorite = ? WHERE path = ?",
        (1 if is_favorite else 0, path),
    )
    conn.commit()


def get_all_songs(conn):
    """
    Return all songs, sorted by filename, as a list of tuples:
    (path, filename, title, artist, album, duration,
     genre, bpm, style, mood, sounds_like, is_favorite)
    """
    return search_songs(conn)


def search_songs(
    conn,
    search_text="",
    genre="",
    style="",
    mood="",
    sounds_like="",
    bpm_min=None,
    bpm_max=None,
    favorites_only=False,
):
    """
    Return songs matching the given filters, combined with AND (all
    provided filters must match). Any filter left blank/None/False is
    skipped entirely, so calling this with no arguments returns the full
    library - this is a read-only query and never changes the underlying
    data.

    - search_text: matched against title, artist, filename, and sounds_like
      (OR) - filename is included so untagged files (no embedded Title)
      can still be found by their filename, the same text shown in the table
    - genre / style / mood / sounds_like: case-insensitive substring match
    - bpm_min / bpm_max: inclusive range; songs with no BPM set are
      excluded from a range search, which is the expected behavior
    - favorites_only: when True, only songs marked as a favorite are returned
    """
    query = """
        SELECT path, filename, title, artist, album, duration,
               genre, bpm, style, mood, sounds_like, is_favorite
        FROM songs
        WHERE 1=1
    """
    params = []

    if search_text:
        query += " AND (title LIKE ? OR artist LIKE ? OR sounds_like LIKE ? OR filename LIKE ?)"
        like_value = f"%{search_text}%"
        params += [like_value, like_value, like_value, like_value]

    if genre:
        query += " AND genre LIKE ?"
        params.append(f"%{genre}%")

    if style:
        query += " AND style LIKE ?"
        params.append(f"%{style}%")

    if mood:
        query += " AND mood LIKE ?"
        params.append(f"%{mood}%")

    if sounds_like:
        query += " AND sounds_like LIKE ?"
        params.append(f"%{sounds_like}%")

    if bpm_min is not None:
        query += " AND bpm >= ?"
        params.append(bpm_min)

    if bpm_max is not None:
        query += " AND bpm <= ?"
        params.append(bpm_max)

    if favorites_only:
        query += " AND is_favorite = 1"

    query += " ORDER BY filename COLLATE NOCASE"

    return conn.execute(query, params).fetchall()
