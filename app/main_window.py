"""
Main application window for the Music Library app.

This module defines the visual shell plus scanning, tag editing,
search/filtering, playback, and favorites:
    - A menu bar
    - A left-hand navigation panel: All Songs / Favorites (switches the
      library view between everything and favorites-only)
    - An "Add Music Folder" button that scans a folder for audio files
    - A search/filter bar (title/artist/sounds-like search, plus Genre,
      Style, Mood, Sounds Like, and BPM range filters, all combinable)
    - A central track list, populated from the SQLite database, with a
      clickable Favorite (star) column and clear "missing file" indicators
    - A tag editor panel below the track list: select a song, edit its
      Genre / BPM / Style / Mood / Sounds Like, and save
    - A playback bar spanning the bottom of the window: Play / Pause /
      Stop, a seekable progress slider, and the current song's name
    - A status bar showing the total song count
"""

import os

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QFormLayout,
    QGridLayout,
    QListWidget,
    QTableWidget,
    QTableWidgetItem,
    QLabel,
    QSplitter,
    QHeaderView,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QLineEdit,
    QSlider,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDoubleValidator
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from app import database, scanner


class MainWindow(QMainWindow):
    """The application's main window."""

    # Index of the clickable Favorite (star) column in the track table.
    FAVORITE_COLUMN = 5

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Music Library")
        self.resize(1000, 650)

        self._favorites_only = False

        self._build_menu_bar()
        self._build_central_widget()
        self._build_status_bar()
        self._setup_media_player()

        # Load any tracks already in the database from a previous run.
        self._apply_filters()

    # ------------------------------------------------------------------
    # UI construction helpers
    # ------------------------------------------------------------------

    def _build_menu_bar(self) -> None:
        """Create the menu bar."""
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        file_menu.addAction("Add Music Folder...", self._on_add_music_folder)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        view_menu = menu_bar.addMenu("&View")
        view_menu.addAction("Refresh Library", self._apply_filters)

        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction("About", self._on_about_clicked)

    def _on_about_clicked(self) -> None:
        """Show a brief About dialog."""
        QMessageBox.information(
            self,
            "About Music Library",
            "Music Library\n\n"
            "A simple local music library manager.\n\n"
            "Scan a folder to import your music, tag songs with Genre, "
            "BPM, Style, Mood, and Sounds Like, search and filter your "
            "collection, preview playback, and mark favorites - all "
            "stored locally on this computer.",
        )

    def _build_central_widget(self) -> None:
        """
        Main layout: a sidebar + track area on top (in a splitter), and a
        persistent playback bar spanning the full width at the bottom.
        """
        splitter = QSplitter(Qt.Horizontal)

        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_track_list())

        # Give the track list most of the horizontal space.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 800])

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(splitter)
        central_layout.addWidget(self._build_player_bar())

        self.setCentralWidget(central)

    def _build_sidebar(self) -> QWidget:
        """Left-hand navigation: switch between All Songs and Favorites."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel("Library")
        title.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(title)

        add_folder_button = QPushButton("Add Music Folder")
        add_folder_button.setToolTip("Scan a folder on your computer for music files")
        add_folder_button.clicked.connect(self._on_add_music_folder)
        layout.addWidget(add_folder_button)

        nav_list = QListWidget()
        nav_list.addItems(["All Songs", "Favorites"])
        # Set the initial selection before connecting the signal, so
        # this line doesn't try to fire _on_nav_selection_changed before
        # the rest of the UI (like the track table) exists yet.
        nav_list.setCurrentRow(0)
        nav_list.currentRowChanged.connect(self._on_nav_selection_changed)
        layout.addWidget(nav_list)

        return container

    def _on_nav_selection_changed(self, row: int) -> None:
        """Switch the library view between "All Songs" (0) and "Favorites" (1)."""
        self._favorites_only = (row == 1)
        self._apply_filters()

    def _build_track_list(self) -> QWidget:
        """Central area: filter bar, track table, then the tag editor panel."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)

        heading = QLabel("All Songs")
        heading.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(heading)

        layout.addWidget(self._build_filter_bar())

        self.track_table = QTableWidget(0, 6)
        self.track_table.setHorizontalHeaderLabels(
            ["Title", "Artist", "Album", "Duration", "Path", "Favorite"]
        )
        self.track_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self.track_table.horizontalHeaderItem(self.FAVORITE_COLUMN).setToolTip(
            "Click the star to mark or unmark a song as a favorite"
        )
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.track_table.setSelectionBehavior(QTableWidget.SelectRows)
        # Readability/responsiveness polish for a library with many songs:
        # alternating row colors make rows easier to track visually, and
        # disabling word wrap keeps long file paths from growing row
        # height and breaking the table's layout.
        self.track_table.setAlternatingRowColors(True)
        self.track_table.setWordWrap(False)
        self.track_table.itemSelectionChanged.connect(self._on_song_selection_changed)
        self.track_table.cellClicked.connect(self._on_table_cell_clicked)
        layout.addWidget(self.track_table)

        layout.addWidget(self._build_tag_editor())

        return container

    def _build_player_bar(self) -> QWidget:
        """
        A simple, always-visible playback bar: current song name, Play /
        Pause / Stop, and a seekable progress slider with time labels.
        Built on QMediaPlayer (part of QtMultimedia, bundled with
        PySide6 - no extra dependency needed).
        """
        box = QGroupBox("Now Playing")
        layout = QHBoxLayout()

        self.now_playing_label = QLabel("No song playing")
        self.now_playing_label.setMinimumWidth(220)
        layout.addWidget(self.now_playing_label)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._on_play_clicked)
        layout.addWidget(self.play_button)

        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self._on_pause_clicked)
        self.pause_button.setEnabled(False)
        layout.addWidget(self.pause_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self._on_stop_clicked)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.stop_button)

        self.current_time_label = QLabel("0:00")
        layout.addWidget(self.current_time_label)

        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 0)
        self.progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self.progress_slider.sliderMoved.connect(self._on_slider_moved)
        self.progress_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self.progress_slider, 1)

        self.total_time_label = QLabel("0:00")
        layout.addWidget(self.total_time_label)

        box.setLayout(layout)
        return box

    def _build_filter_bar(self) -> QWidget:
        """
        Search box plus per-field filters. All filters combine with AND
        (e.g. Genre + Mood + Style all applied together), and every field
        updates the list live as the user types - no Search button needed.
        """
        box = QGroupBox("Search / Filter")
        grid = QGridLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search title, artist, or sounds like...")
        self.search_input.textChanged.connect(self._apply_filters)
        grid.addWidget(QLabel("Search:"), 0, 0)
        grid.addWidget(self.search_input, 0, 1, 1, 5)

        self.genre_filter = QLineEdit()
        self.genre_filter.textChanged.connect(self._apply_filters)
        grid.addWidget(QLabel("Genre:"), 1, 0)
        grid.addWidget(self.genre_filter, 1, 1)

        self.style_filter = QLineEdit()
        self.style_filter.textChanged.connect(self._apply_filters)
        grid.addWidget(QLabel("Style:"), 1, 2)
        grid.addWidget(self.style_filter, 1, 3)

        self.mood_filter = QLineEdit()
        self.mood_filter.textChanged.connect(self._apply_filters)
        grid.addWidget(QLabel("Mood:"), 1, 4)
        grid.addWidget(self.mood_filter, 1, 5)

        self.sounds_like_filter = QLineEdit()
        self.sounds_like_filter.textChanged.connect(self._apply_filters)
        grid.addWidget(QLabel("Sounds Like:"), 2, 0)
        grid.addWidget(self.sounds_like_filter, 2, 1)

        bpm_validator = QDoubleValidator(0.0, 999.0, 2, self)

        self.bpm_min_filter = QLineEdit()
        self.bpm_min_filter.setPlaceholderText("Min")
        self.bpm_min_filter.setValidator(bpm_validator)
        self.bpm_min_filter.textChanged.connect(self._apply_filters)
        grid.addWidget(QLabel("BPM:"), 2, 2)
        grid.addWidget(self.bpm_min_filter, 2, 3)

        self.bpm_max_filter = QLineEdit()
        self.bpm_max_filter.setPlaceholderText("Max")
        self.bpm_max_filter.setValidator(bpm_validator)
        self.bpm_max_filter.textChanged.connect(self._apply_filters)
        grid.addWidget(QLabel("to:"), 2, 4)
        grid.addWidget(self.bpm_max_filter, 2, 5)

        clear_button = QPushButton("Clear Filters")
        clear_button.clicked.connect(self._on_clear_filters_clicked)
        grid.addWidget(clear_button, 0, 6, 3, 1)

        box.setLayout(grid)
        return box

    def _build_tag_editor(self) -> QWidget:
        """
        A simple panel for viewing/editing the tags of whichever song is
        currently selected in the track table. Disabled until a song is
        selected. Kept as plain text fields on purpose - with 1,000+ songs
        the goal is quick, low-friction editing, not a fancy dialog.
        """
        self.tag_editor_box = QGroupBox("No song selected")

        form = QFormLayout()

        self.genre_input = QLineEdit()
        self.bpm_input = QLineEdit()
        self.bpm_input.setPlaceholderText("e.g. 120")
        # Allow decimal numbers only (with an optional leading minus, though
        # negative BPM doesn't make sense - the save step also validates).
        self.bpm_input.setValidator(QDoubleValidator(0.0, 999.0, 2, self.bpm_input))
        self.style_input = QLineEdit()
        self.mood_input = QLineEdit()
        self.sounds_like_input = QLineEdit()
        self.sounds_like_input.setPlaceholderText("Artist, song, or both")

        form.addRow("Genre:", self.genre_input)
        form.addRow("BPM / Tempo:", self.bpm_input)
        form.addRow("Style:", self.style_input)
        form.addRow("Mood:", self.mood_input)
        form.addRow("Sounds Like:", self.sounds_like_input)

        self.save_tags_button = QPushButton("Save Tags")
        self.save_tags_button.clicked.connect(self._on_save_tags_clicked)
        form.addRow("", self.save_tags_button)

        self.tag_editor_box.setLayout(form)
        self.tag_editor_box.setEnabled(False)

        return self.tag_editor_box

    def _build_status_bar(self) -> None:
        """Simple status bar showing a placeholder track count."""
        self.statusBar().showMessage("0 songs")

    # ------------------------------------------------------------------
    # Folder scanning
    # ------------------------------------------------------------------

    def _on_add_music_folder(self) -> None:
        """Prompt the user for a folder, then scan it for audio files."""
        folder_path = QFileDialog.getExistingDirectory(self, "Select Music Folder")
        if not folder_path:
            return  # user cancelled
        self._scan_folder(folder_path)

    def _scan_folder(self, folder_path: str) -> None:
        """Scan folder_path recursively and add any new audio files to the database."""
        conn = database.get_connection()

        found = 0
        skipped = 0

        for file_path in scanner.find_audio_files(folder_path):
            found += 1
            try:
                meta = scanner.read_metadata(file_path)
                database.add_song(
                    conn,
                    path=file_path,
                    filename=os.path.basename(file_path),
                    title=meta["title"],
                    artist=meta["artist"],
                    album=meta["album"],
                    duration=meta["duration"],
                )
            except Exception:
                # Never let one bad file stop the whole scan.
                skipped += 1
                continue

        conn.commit()
        conn.close()

        self._apply_filters()

        if found == 0:
            QMessageBox.information(
                self, "Scan Complete", "No supported audio files were found in that folder."
            )
        elif skipped:
            self.statusBar().showMessage(
                f"Scan complete - {found} file(s) found, {skipped} could not be read", 5000
            )
        else:
            self.statusBar().showMessage(f"Scan complete - {found} file(s) found", 5000)

    # ------------------------------------------------------------------
    # Search / filtering
    # ------------------------------------------------------------------

    def _apply_filters(self) -> None:
        """
        Re-query the database using whatever is currently in the search/
        filter fields (plus the All Songs / Favorites sidebar selection)
        and refresh the table. This is read-only - it never changes any
        saved song data, it only changes what's displayed. Called
        automatically whenever a filter field or the sidebar changes.
        """
        conn = database.get_connection()
        songs = database.search_songs(
            conn,
            search_text=self.search_input.text().strip(),
            genre=self.genre_filter.text().strip(),
            style=self.style_filter.text().strip(),
            mood=self.mood_filter.text().strip(),
            sounds_like=self.sounds_like_filter.text().strip(),
            bpm_min=self._parse_optional_float(self.bpm_min_filter.text()),
            bpm_max=self._parse_optional_float(self.bpm_max_filter.text()),
            favorites_only=self._favorites_only,
        )
        conn.close()
        self._populate_table(songs)

    def _on_clear_filters_clicked(self) -> None:
        """Reset every filter field and show the full library again."""
        filter_fields = (
            self.search_input,
            self.genre_filter,
            self.style_filter,
            self.mood_filter,
            self.sounds_like_filter,
            self.bpm_min_filter,
            self.bpm_max_filter,
        )
        for field in filter_fields:
            field.blockSignals(True)
            field.clear()
            field.blockSignals(False)
        self._apply_filters()

    @staticmethod
    def _parse_optional_float(text: str):
        """Parse text as a float for a filter field; blank or invalid -> None (no bound)."""
        text = text.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _populate_table(self, songs) -> None:
        """Fill the track table with the given list of song rows."""
        # Remember what was selected (by path, not row index - row indices
        # shift as the table is rebuilt) so we can restore it afterward.
        # Without this, any refresh (rescan, a filter changing, clicking
        # Refresh Library) while a song is selected in the tag editor
        # would silently drop the selection, leaving the tag editor
        # showing stale data and "Save Tags" doing nothing with no
        # feedback to the user.
        previously_selected_path = self._get_selected_song_path()

        # Turning off updates during a bulk repopulate avoids flicker and
        # keeps the UI responsive even with a large (1,000+ song) library.
        self.track_table.setUpdatesEnabled(False)
        self.track_table.setRowCount(0)
        missing_count = 0
        row_to_reselect = None

        for row_index, song in enumerate(songs):
            (path, filename, title, artist, album, duration,
             _genre, _bpm, _style, _mood, _sounds_like, is_favorite) = song

            is_missing = not os.path.isfile(path)
            if is_missing:
                missing_count += 1

            display_title = title or filename
            if is_missing:
                display_title = f"⚠ {display_title}"
            display_path = f"{path}  (file not found)" if is_missing else path

            self.track_table.insertRow(row_index)

            title_item = QTableWidgetItem(display_title)
            title_item.setToolTip(display_title)
            # Store the real, unmodified path on the row so it can always
            # be looked up reliably regardless of what the visible text
            # says (missing files get extra text appended above).
            title_item.setData(Qt.UserRole, path)
            self.track_table.setItem(row_index, 0, title_item)

            self.track_table.setItem(row_index, 1, QTableWidgetItem(artist or ""))
            self.track_table.setItem(row_index, 2, QTableWidgetItem(album or ""))
            self.track_table.setItem(row_index, 3, QTableWidgetItem(self._format_duration(duration)))

            path_item = QTableWidgetItem(display_path)
            path_item.setToolTip(display_path)
            self.track_table.setItem(row_index, 4, path_item)

            favorite_item = QTableWidgetItem("★" if is_favorite else "☆")
            favorite_item.setTextAlignment(Qt.AlignCenter)
            favorite_item.setToolTip("Favorite" if is_favorite else "Not a favorite - click to mark")
            self.track_table.setItem(row_index, self.FAVORITE_COLUMN, favorite_item)

            if is_missing:
                self._mark_row_as_missing(row_index)

            if path == previously_selected_path:
                row_to_reselect = row_index

        self.track_table.setUpdatesEnabled(True)

        if row_to_reselect is not None:
            # Re-select the same song by path. This re-triggers the normal
            # selection-changed handler, which reloads its tags fresh from
            # the database - so the tag editor stays in sync rather than
            # showing potentially-stale text.
            self.track_table.selectRow(row_to_reselect)
        elif previously_selected_path is not None:
            # The previously selected song is no longer in view (filtered
            # out) - reset the tag editor instead of leaving it showing
            # a song that's no longer selectable.
            self._clear_tag_editor()

        if missing_count:
            self.statusBar().showMessage(f"{len(songs)} songs shown ({missing_count} file(s) missing)")
        else:
            self.statusBar().showMessage(f"{len(songs)} songs shown")

    def _mark_row_as_missing(self, row_index: int) -> None:
        """Gray out a row whose file can no longer be found on disk."""
        gray = QColor("gray")
        for column in range(self.track_table.columnCount()):
            item = self.track_table.item(row_index, column)
            if item is not None:
                item.setForeground(gray)

    def _on_table_cell_clicked(self, row: int, column: int) -> None:
        """Toggle a song's favorite status when its star cell is clicked."""
        if column != self.FAVORITE_COLUMN:
            return

        title_item = self.track_table.item(row, 0)
        if title_item is None:
            return
        path = title_item.data(Qt.UserRole)
        if path is None:
            return

        favorite_item = self.track_table.item(row, column)
        is_now_favorite = favorite_item.text() != "★"  # flip current state

        conn = database.get_connection()
        database.set_favorite(conn, path, is_now_favorite)
        conn.close()

        favorite_item.setText("★" if is_now_favorite else "☆")
        favorite_item.setToolTip("Favorite" if is_now_favorite else "Not a favorite - click to mark")

        # If we're looking at "Favorites" only and just unmarked a song,
        # it no longer belongs in this view - refresh to remove it.
        if self._favorites_only and not is_now_favorite:
            self._apply_filters()

    @staticmethod
    def _format_duration(seconds) -> str:
        """Format a duration in seconds as m:ss. Returns an empty string if unknown."""
        if not seconds:
            return ""
        seconds = int(seconds)
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}:{secs:02d}"

    # ------------------------------------------------------------------
    # Tag editing
    # ------------------------------------------------------------------

    def _get_selected_song_path(self):
        """Return the real file path of the currently selected row, or None."""
        row = self.track_table.currentRow()
        if row < 0:
            return None
        title_item = self.track_table.item(row, 0)
        if title_item is None:
            return None
        return title_item.data(Qt.UserRole)

    def _on_song_selection_changed(self) -> None:
        """When the selected song changes, load its saved tags into the editor."""
        path = self._get_selected_song_path()

        if path is None:
            self._clear_tag_editor()
            return

        conn = database.get_connection()
        tags = database.get_song_tags(conn, path)
        conn.close()

        if tags is None:
            self._clear_tag_editor()
            return

        genre, bpm, style, mood, sounds_like = tags
        self.genre_input.setText(genre or "")
        self.bpm_input.setText(self._format_bpm(bpm))
        self.style_input.setText(style or "")
        self.mood_input.setText(mood or "")
        self.sounds_like_input.setText(sounds_like or "")

        row = self.track_table.currentRow()
        song_title = self.track_table.item(row, 0).text()
        self.tag_editor_box.setTitle(f"Tags for: {song_title}")
        self.tag_editor_box.setEnabled(True)

    def _clear_tag_editor(self) -> None:
        """Blank out and disable the tag editor when nothing is selected."""
        self.genre_input.clear()
        self.bpm_input.clear()
        self.style_input.clear()
        self.mood_input.clear()
        self.sounds_like_input.clear()
        self.tag_editor_box.setTitle("No song selected")
        self.tag_editor_box.setEnabled(False)

    def _on_save_tags_clicked(self) -> None:
        """Validate and save the tag editor's current values for the selected song."""
        path = self._get_selected_song_path()
        if path is None:
            return  # nothing selected - Save button shouldn't be reachable, but be safe

        bpm_text = self.bpm_input.text().strip()
        bpm_value = None
        if bpm_text:
            try:
                bpm_value = float(bpm_text)
            except ValueError:
                QMessageBox.warning(
                    self,
                    "Invalid BPM",
                    "BPM must be a number, like 120 or 128.5. Please correct it and try again.",
                )
                return

        conn = database.get_connection()
        database.update_song_tags(
            conn,
            path=path,
            genre=self.genre_input.text().strip() or None,
            bpm=bpm_value,
            style=self.style_input.text().strip() or None,
            mood=self.mood_input.text().strip() or None,
            sounds_like=self.sounds_like_input.text().strip() or None,
        )
        conn.close()

        # Reflect the cleaned-up BPM formatting back into the field.
        self.bpm_input.setText(self._format_bpm(bpm_value))
        self.statusBar().showMessage("Tags saved", 3000)

    @staticmethod
    def _format_bpm(bpm) -> str:
        """Format a BPM value for display: whole numbers show without a decimal."""
        if bpm is None:
            return ""
        if float(bpm).is_integer():
            return str(int(bpm))
        return str(bpm)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _setup_media_player(self) -> None:
        """Create the QMediaPlayer and wire up its signals. Called once at startup."""
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)

        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.errorOccurred.connect(self._on_media_error)

        # Track what's loaded separately from the table selection, so
        # playback keeps working even if the user selects a different
        # row (or the list gets filtered) while a song is playing.
        self._current_playback_path = None
        self._current_playback_title = None
        self._slider_being_dragged = False

    def _on_play_clicked(self) -> None:
        """Play the selected song, or resume it if it's the one already loaded and paused."""
        path = self._get_selected_song_path()
        if path is None:
            QMessageBox.information(self, "No Song Selected", "Select a song in the list first.")
            return

        if not os.path.isfile(path):
            QMessageBox.warning(
                self,
                "File Unavailable",
                "This song's file could not be found on disk. It may have been moved or deleted.",
            )
            return

        if path != self._current_playback_path:
            # A different song than what's currently loaded (or nothing
            # was loaded yet) - switch playback to it from the start.
            row = self.track_table.currentRow()
            self._current_playback_path = path
            self._current_playback_title = self.track_table.item(row, 0).text()
            self.media_player.setSource(QUrl.fromLocalFile(path))

        self.media_player.play()

    def _on_pause_clicked(self) -> None:
        self.media_player.pause()

    def _on_stop_clicked(self) -> None:
        self.media_player.stop()

    def _on_position_changed(self, position_ms: int) -> None:
        if not self._slider_being_dragged:
            self.progress_slider.setValue(position_ms)
        self.current_time_label.setText(self._format_duration(position_ms / 1000))

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.progress_slider.setRange(0, duration_ms)
        self.total_time_label.setText(self._format_duration(duration_ms / 1000))

    def _on_slider_pressed(self) -> None:
        self._slider_being_dragged = True

    def _on_slider_moved(self, position_ms: int) -> None:
        # Update the time label live while dragging, without seeking yet.
        self.current_time_label.setText(self._format_duration(position_ms / 1000))

    def _on_slider_released(self) -> None:
        self._slider_being_dragged = False
        self.media_player.setPosition(self.progress_slider.value())

    def _on_playback_state_changed(self, state) -> None:
        """Keep button enabled/disabled state and the "Now Playing" label in sync."""
        is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        is_paused = state == QMediaPlayer.PlaybackState.PausedState

        self.play_button.setEnabled(not is_playing)
        self.pause_button.setEnabled(is_playing)
        self.stop_button.setEnabled(is_playing or is_paused)

        if not self._current_playback_title:
            self.now_playing_label.setText("No song playing")
            return

        if is_playing:
            prefix = "Playing"
        elif is_paused:
            prefix = "Paused"
        else:
            prefix = "Stopped"
        self.now_playing_label.setText(f"{prefix}: {self._current_playback_title}")

    def _on_media_error(self, error, error_string) -> None:
        """Handle playback errors (corrupt file, unsupported codec, etc.) without crashing."""
        if error == QMediaPlayer.Error.NoError:
            return
        QMessageBox.warning(
            self,
            "Playback Error",
            f"This song could not be played.\n\n{error_string}",
        )
        self.media_player.stop()
        self.now_playing_label.setText("No song playing")
        self._current_playback_path = None
        self._current_playback_title = None
