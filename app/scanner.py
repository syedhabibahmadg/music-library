"""
Folder scanning and basic metadata reading.

- `find_audio_files` walks a folder tree and yields paths to files with
  a supported audio extension.
- `read_metadata` uses Mutagen to pull title/artist/album/duration out
  of a single file. It never raises - if a file is corrupt, unsupported,
  or unreadable, it just returns empty metadata so the caller can carry
  on with the rest of the scan.
"""

import os

from mutagen import File as MutagenFile

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".wma", ".aac"}


def find_audio_files(folder_path):
    """Recursively yield full paths to supported audio files under folder_path."""
    for root, _dirs, files in os.walk(folder_path):
        for name in files:
            if os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS:
                yield os.path.join(root, name)


def read_metadata(file_path):
    """
    Try to read title/artist/album/duration from an audio file.

    Returns a dict with whatever could be read; fields that aren't
    available (or that fail to read) are left as None. This function
    never raises - callers can rely on always getting a dict back.
    """
    metadata = {"title": None, "artist": None, "album": None, "duration": None}

    try:
        audio = MutagenFile(file_path, easy=True)
        if audio is None:
            return metadata

        if audio.tags:
            title = audio.tags.get("title")
            artist = audio.tags.get("artist")
            album = audio.tags.get("album")
            metadata["title"] = title[0] if title else None
            metadata["artist"] = artist[0] if artist else None
            metadata["album"] = album[0] if album else None

        if audio.info is not None and hasattr(audio.info, "length"):
            metadata["duration"] = audio.info.length

    except Exception:
        # Corrupt file, unsupported format variant, permission issue, etc.
        # Skip metadata for this file rather than crashing the scan.
        pass

    return metadata
