from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from meeting_copilot.controller import MeetingAssistantController
from meeting_copilot.models import TranscriptSegment


class ResizeHandle(QWidget):
    def __init__(self, window: "MainWindow", edges: Qt.Edge | Qt.Corner, thickness: int) -> None:
        super().__init__(window)
        self._window = window
        self._edges = edges
        self._thickness = thickness
        self.setMouseTracking(True)
        self.setCursor(self._cursor_for_edges(edges))
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._window._begin_resize(self._edges, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.buttons() & Qt.LeftButton:
            self._window._handle_resize_move(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._window._end_resize()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @staticmethod
    def _cursor_for_edges(edges: Qt.Edge | Qt.Corner) -> Qt.CursorShape:
        if edges in (Qt.TopEdge, Qt.BottomEdge):
            return Qt.SizeVerCursor
        if edges in (Qt.LeftEdge, Qt.RightEdge):
            return Qt.SizeHorCursor
        if edges in (Qt.TopLeftCorner, Qt.BottomRightCorner):
            return Qt.SizeFDiagCursor
        return Qt.SizeBDiagCursor


class MainWindow(QMainWindow):
    def __init__(self, controller: MeetingAssistantController) -> None:
        super().__init__()
        self._controller = controller
        self._segments_by_id: dict[str, TranscriptSegment] = {}
        self._segment_order: list[str] = []
        self._current_partial_english = ""
        self._current_partial_chinese = ""
        self._status_full_text = "准备就绪。点击 Start 开始。"
        self._drag_offset: QPoint | None = None
        self._resize_origin: QPoint | None = None
        self._resize_start_geometry: QRect | None = None
        self._resize_edges: Qt.Edge | Qt.Corner | None = None
        self._pinned = True
        self._compact_height = 400
        self._expanded_height = 620
        self._resize_margin = 10
        self._resize_handles: list[ResizeHandle] = []

        self.setWindowTitle("Meeting Copilot")
        self.resize(900, self._compact_height)
        self.setMinimumSize(680, 300)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._apply_window_flags(initial=True)
        self._build_ui()
        self._build_resize_handles()
        self._bind_signals()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("GlassShell")
        shell_shadow = QGraphicsDropShadowEffect(self.shell)
        shell_shadow.setBlurRadius(32)
        shell_shadow.setOffset(0, 12)
        shell_shadow.setColor(QColor(0, 0, 0, 84))
        self.shell.setGraphicsEffect(shell_shadow)
        root_layout.addWidget(self.shell)

        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(16, 16, 16, 16)
        shell_layout.setSpacing(10)

        self.header_bar = QFrame()
        self.header_bar.setObjectName("HeaderBar")
        header_layout = QHBoxLayout(self.header_bar)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        left_cluster = QHBoxLayout()
        left_cluster.setContentsMargins(0, 0, 0, 0)
        left_cluster.setSpacing(8)

        self.start_button = self._make_chip_button("Start", primary=True)
        self.stop_button = self._make_chip_button("Stop", danger=True)
        self.stop_button.setEnabled(False)

        self.status_chip = QLabel("Ready")
        self.status_chip.setObjectName("StatusChip")

        left_cluster.addWidget(self.start_button)
        left_cluster.addWidget(self.stop_button)
        left_cluster.addWidget(self.status_chip)

        middle_cluster = QHBoxLayout()
        middle_cluster.setContentsMargins(0, 0, 0, 0)
        middle_cluster.setSpacing(8)

        self.language_chip = QLabel("English  ->  Chinese")
        self.language_chip.setObjectName("LanguageChip")
        self.status_label = QLabel(self._status_full_text)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(False)
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setAlignment(Qt.AlignHCenter)
        self.status_label.setMaximumWidth(320)

        middle_wrap = QVBoxLayout()
        middle_wrap.setContentsMargins(0, 0, 0, 0)
        middle_wrap.setSpacing(4)
        middle_wrap.addWidget(self.language_chip, 0, Qt.AlignHCenter)
        middle_wrap.addWidget(self.status_label, 0, Qt.AlignHCenter)

        right_cluster = QHBoxLayout()
        right_cluster.setContentsMargins(0, 0, 0, 0)
        right_cluster.setSpacing(8)

        self.pin_button = self._make_window_button("Pin")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(True)
        self.pin_button.setObjectName("PinButton")

        self.close_button = self._make_window_button("Close")
        self.close_button.setObjectName("CloseButton")

        right_cluster.addWidget(self.pin_button)
        right_cluster.addWidget(self.close_button)

        header_layout.addLayout(left_cluster)
        header_layout.addLayout(middle_wrap, 1)
        header_layout.addLayout(right_cluster)
        shell_layout.addWidget(self.header_bar)

        self.subtitle_card = QFrame()
        self.subtitle_card.setObjectName("SubtitleCard")
        subtitle_layout = QVBoxLayout(self.subtitle_card)
        subtitle_layout.setContentsMargins(0, 0, 0, 0)
        subtitle_layout.setSpacing(0)

        self.subtitle_view = self._make_browser(min_height=170)
        subtitle_layout.addWidget(self.subtitle_view)

        shell_layout.addWidget(self.subtitle_card, 1)

        self.assistant_card = QFrame()
        self.assistant_card.setObjectName("AssistantCard")
        assistant_layout = QVBoxLayout(self.assistant_card)
        assistant_layout.setContentsMargins(16, 16, 16, 16)
        assistant_layout.setSpacing(8)

        assistant_header = QHBoxLayout()
        assistant_header.setContentsMargins(0, 0, 0, 0)
        assistant_header.setSpacing(10)

        self.assistant_title = QLabel("Assistant")
        self.assistant_title.setObjectName("AssistantTitle")

        self.dismiss_assistant_button = self._make_window_button("Hide")
        self.dismiss_assistant_button.setObjectName("HideButton")

        assistant_header.addWidget(self.assistant_title)
        assistant_header.addStretch(1)
        assistant_header.addWidget(self.dismiss_assistant_button)

        self.assistant_view = self._make_browser(min_height=220)

        assistant_layout.addLayout(assistant_header)
        assistant_layout.addWidget(self.assistant_view)
        self.assistant_card.setVisible(False)

        shell_layout.addWidget(self.assistant_card)

        action_bar = QFrame()
        action_bar.setObjectName("ActionBar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)

        self.question_button = self._make_chip_button("Question")
        self.summary_button = self._make_chip_button("Summary")
        self.save_button = self._make_chip_button("Save")
        self.clear_button = self._make_chip_button("Clear")

        for button in (self.question_button, self.summary_button, self.save_button, self.clear_button):
            action_layout.addWidget(button)

        action_layout.addStretch(1)

        grip = QSizeGrip(self.shell)
        grip.setFixedSize(14, 14)
        action_layout.addWidget(grip, 0, Qt.AlignRight | Qt.AlignBottom)

        shell_layout.addWidget(action_bar)

        self.setStyleSheet(
            """
            QWidget#Root {
                background: transparent;
                color: #f5f7fb;
                font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", "Helvetica Neue", sans-serif;
                font-size: 13px;
            }
            QFrame#GlassShell {
                background: rgba(20, 24, 30, 224);
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 14px;
            }
            QFrame#SubtitleCard {
                background: transparent;
                border: none;
            }
            QFrame#AssistantCard {
                background: rgba(255, 255, 255, 12);
                border: 1px solid rgba(255, 255, 255, 16);
                border-radius: 12px;
            }
            QLabel#StatusChip,
            QLabel#LanguageChip {
                background: rgba(255, 255, 255, 16);
                color: rgba(247, 250, 255, 210);
                border: 1px solid rgba(255, 255, 255, 24);
                border-radius: 12px;
                padding: 6px 12px;
                font-weight: 600;
            }
            QLabel#StatusLabel {
                color: rgba(245, 249, 255, 168);
                font-size: 12px;
                font-weight: 400;
            }
            QLabel#AssistantTitle {
                color: #f8fbff;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton {
                background: rgba(255, 255, 255, 16);
                color: #f6f8fb;
                border: 1px solid rgba(255, 255, 255, 24);
                border-radius: 16px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 24);
            }
            QPushButton:disabled {
                background: rgba(255, 255, 255, 10);
                color: rgba(246, 248, 251, 96);
            }
            QPushButton#PrimaryChip {
                background: rgba(96, 162, 255, 44);
                border: 1px solid rgba(96, 162, 255, 90);
            }
            QPushButton#DangerChip {
                background: rgba(255, 92, 92, 44);
                border: 1px solid rgba(255, 92, 92, 90);
            }
            QToolButton {
                background: rgba(255, 255, 255, 14);
                color: rgba(247, 250, 255, 214);
                border: 1px solid rgba(255, 255, 255, 22);
                border-radius: 14px;
                padding: 7px 12px;
                font-weight: 600;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 24);
            }
            QToolButton#PinButton:checked {
                background: rgba(120, 182, 255, 44);
                border: 1px solid rgba(120, 182, 255, 90);
            }
            QToolButton#CloseButton:hover {
                background: rgba(255, 92, 92, 54);
                border: 1px solid rgba(255, 92, 92, 110);
            }
            QTextBrowser {
                background: transparent;
                color: #f5f7fb;
                border: none;
                padding: 0px;
                font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", "Helvetica Neue", sans-serif;
                selection-background-color: rgba(114, 171, 255, 115);
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 2px 0 2px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 40);
                border-radius: 4px;
                min-height: 18px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
                height: 0px;
            }
            QSizeGrip {
                width: 14px;
                height: 14px;
            }
            """
        )

        self._render_bilingual_feed()

    def _bind_signals(self) -> None:
        self.start_button.clicked.connect(self._controller.start)
        self.stop_button.clicked.connect(self._controller.stop)
        self.question_button.clicked.connect(self._controller.suggest_questions)
        self.summary_button.clicked.connect(self._controller.summarize)
        self.save_button.clicked.connect(self._controller.save_transcript)
        self.clear_button.clicked.connect(self._clear_all)
        self.pin_button.toggled.connect(self._set_pinned)
        self.close_button.clicked.connect(self.close)
        self.dismiss_assistant_button.clicked.connect(self._hide_assistant)

        self._controller.signals.saved.connect(self._show_saved)
        self._controller.signals.status.connect(self._set_status_message)
        self._controller.signals.partial_english.connect(self._set_partial_english)
        self._controller.signals.partial_chinese.connect(self._set_partial_chinese)
        self._controller.signals.assistant.connect(self._set_assistant_content)
        self._controller.signals.segment_changed.connect(self._upsert_segment)
        self._controller.signals.running.connect(self._toggle_running_state)
        self._controller.signals.error.connect(self._show_error)

    def _set_status_message(self, text: str) -> None:
        self._status_full_text = text.strip() or "准备就绪。点击 Start 开始。"
        self._refresh_status_label()

    def _set_partial_english(self, text: str) -> None:
        self._current_partial_english = text.strip()
        self._render_bilingual_feed()

    def _set_partial_chinese(self, text: str) -> None:
        self._current_partial_chinese = text.strip()
        self._render_bilingual_feed()

    def _set_assistant_content(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return

        title = str(payload.get("title", "")).strip()
        body = str(payload.get("body", "")).strip()
        if not title and not body:
            self._hide_assistant()
            return

        self.assistant_title.setText(title or "Assistant")
        self.assistant_view.setHtml(
            self._wrap_html(
                f"<div class='assistant-copy'>{self._multiline_to_html(body)}</div>"
            )
        )
        self.assistant_card.setVisible(True)
        self._set_expanded_mode(True)
        self._scroll_to_bottom(self.assistant_view)

    def _upsert_segment(self, segment: TranscriptSegment) -> None:
        if segment.item_id not in self._segments_by_id:
            self._segment_order.append(segment.item_id)
        self._segments_by_id[segment.item_id] = segment
        self._render_bilingual_feed()

    def _render_bilingual_feed(self) -> None:
        blocks: list[str] = []
        for block in self._group_segments_for_display():
            blocks.append(
                "<div class='entry'>"
                f"<div class='timestamp'>{html.escape(block['timestamp'])}</div>"
                f"<div class='english'>{html.escape(block['english'])}</div>"
                f"<div class='chinese'>{html.escape(block['chinese'])}</div>"
                "</div>"
            )

        show_live = bool(self._current_partial_english or self._current_partial_chinese)
        if show_live and recent_ids:
            latest_segment = self._segments_by_id[recent_ids[-1]]
            english_duplicate = (
                not self._current_partial_english
                or self._current_partial_english == latest_segment.english
            )
            chinese_duplicate = (
                not self._current_partial_chinese
                or self._current_partial_chinese == latest_segment.chinese
            )
            if english_duplicate and chinese_duplicate:
                show_live = False

        if show_live:
            blocks.append(
                "<div class='entry live-entry'>"
                "<div class='live-line'>"
                "<span class='live-badge'>LIVE</span>"
                f"<span class='english live-english'>{html.escape(self._current_partial_english or 'Listening...')}</span>"
                "</div>"
                f"<div class='chinese'>{html.escape(self._current_partial_chinese or '正在翻译...')}</div>"
                "</div>"
            )

        if not blocks:
            blocks.append(
                "<div class='placeholder'>"
                "字幕会在这里按双语形式滚动显示。上方是英文原文，下方是中文译文。"
                "</div>"
            )

        self.subtitle_view.setHtml(self._wrap_html("".join(blocks)))
        self._scroll_to_bottom(self.subtitle_view)

    def _toggle_running_state(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.status_chip.setText("Listening" if running else "Ready")

    def _show_error(self, message: str) -> None:
        self._set_status_message(message)
        QMessageBox.warning(self, "Meeting Copilot", message)

    def _show_saved(self, path: str) -> None:
        QMessageBox.information(self, "Meeting Copilot", f"会议记录已保存到：\n{path}")

    def _set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self._apply_window_flags()

    def _apply_window_flags(self, initial: bool = False) -> None:
        geometry = self.geometry()
        flags = Qt.Window | Qt.FramelessWindowHint
        if self._pinned:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if not initial:
            self.show()
            self.setGeometry(geometry)

    def _hide_assistant(self) -> None:
        self.assistant_card.setVisible(False)
        self.assistant_title.setText("Assistant")
        self.assistant_view.clear()
        self._set_expanded_mode(False)

    def _clear_all(self) -> None:
        self._segments_by_id.clear()
        self._segment_order.clear()
        self._current_partial_english = ""
        self._current_partial_chinese = ""
        self._hide_assistant()
        self.subtitle_view.clear()
        self._controller.clear()
        self._render_bilingual_feed()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._controller.stop()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._layout_resize_handles()
        self._refresh_status_label()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        child = self.childAt(event.position().toPoint())
        if isinstance(child, QAbstractButton):
            return super().mousePressEvent(event)

        if self._header_rect().contains(event.position().toPoint()):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is None or not (event.buttons() & Qt.LeftButton):
            return super().mouseMoveEvent(event)

        self.move(event.globalPosition().toPoint() - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def place_default(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.left() + max(0, (available.width() - self.width()) // 2)
        y = available.bottom() - self.height() - 48
        self.move(x, max(available.top() + 24, y))

    def _set_expanded_mode(self, expanded: bool) -> None:
        screen = QGuiApplication.primaryScreen()
        target_height = self._expanded_height if expanded else self._compact_height
        if screen is not None:
            available = screen.availableGeometry()
            target_height = min(target_height, available.height() - 72)
        if self.height() == target_height:
            return

        geometry = self.geometry()
        new_y = geometry.bottom() - target_height + 1
        self.resize(self.width(), target_height)
        if screen is not None:
            available = screen.availableGeometry()
            new_y = max(available.top() + 24, new_y)
        self.move(geometry.x(), new_y)

    def _group_segments_for_display(self) -> list[dict[str, str]]:
        grouped: list[dict[str, str | datetime | int]] = []
        for item_id in self._segment_order:
            segment = self._segments_by_id[item_id]
            english = segment.english.strip()
            chinese = (segment.chinese or "翻译中...").strip()
            if not english and not chinese:
                continue

            if not grouped:
                grouped.append(
                    {
                        "timestamp": segment.timestamp,
                        "english": english,
                        "chinese": chinese,
                        "segment_count": 1,
                        "mergeable": segment.translation_status == "done",
                        "last_timestamp": segment.timestamp,
                    }
                )
                continue

            current = grouped[-1]
            if self._should_merge_segment(current, segment):
                current["english"] = self._join_english(str(current["english"]), english)
                current["chinese"] = self._join_chinese(str(current["chinese"]), chinese)
                current["segment_count"] = int(current["segment_count"]) + 1
                current["last_timestamp"] = segment.timestamp
            else:
                grouped.append(
                    {
                        "timestamp": segment.timestamp,
                        "english": english,
                        "chinese": chinese,
                        "segment_count": 1,
                        "mergeable": segment.translation_status == "done",
                        "last_timestamp": segment.timestamp,
                    }
                )

        return [
            {
                "timestamp": str(block["timestamp"].strftime("%H:%M:%S")),
                "english": str(block["english"]),
                "chinese": str(block["chinese"]),
            }
            for block in grouped
        ]

    @staticmethod
    def _join_english(existing: str, incoming: str) -> str:
        if not existing:
            return incoming
        if not incoming:
            return existing
        if existing.endswith(("-", "/", "(")) or incoming.startswith((".", ",", ";", ":", "?", "!")):
            return f"{existing}{incoming}"
        return f"{existing} {incoming}"

    @staticmethod
    def _join_chinese(existing: str, incoming: str) -> str:
        if not existing:
            return incoming
        if not incoming:
            return existing
        return f"{existing}{incoming}"

    @staticmethod
    def _should_merge_segment(
        current: dict[str, str | datetime | int | bool],
        segment: TranscriptSegment,
    ) -> bool:
        if not bool(current.get("mergeable", False)):
            return False
        if segment.translation_status != "done":
            return False
        last_timestamp = current.get("last_timestamp")
        if not isinstance(last_timestamp, datetime):
            return False
        gap_seconds = (segment.timestamp - last_timestamp).total_seconds()
        if gap_seconds > 5:
            return False
        if int(current["segment_count"]) >= 5:
            return False
        if len(str(current["english"])) + len(segment.english) > 340:
            return False
        if len(str(current["chinese"])) + len(segment.chinese) > 220:
            return False
        return True

    def _build_resize_handles(self) -> None:
        edges = (
            Qt.TopEdge,
            Qt.BottomEdge,
            Qt.LeftEdge,
            Qt.RightEdge,
            Qt.TopLeftCorner,
            Qt.TopRightCorner,
            Qt.BottomLeftCorner,
            Qt.BottomRightCorner,
        )
        self._resize_handles = [
            ResizeHandle(self, edge, self._resize_margin) for edge in edges
        ]
        self._layout_resize_handles()

    def _layout_resize_handles(self) -> None:
        width = self.width()
        height = self.height()
        margin = self._resize_margin
        geometries = {
            Qt.TopEdge: QRect(margin, 0, max(0, width - 2 * margin), margin),
            Qt.BottomEdge: QRect(margin, max(0, height - margin), max(0, width - 2 * margin), margin),
            Qt.LeftEdge: QRect(0, margin, margin, max(0, height - 2 * margin)),
            Qt.RightEdge: QRect(max(0, width - margin), margin, margin, max(0, height - 2 * margin)),
            Qt.TopLeftCorner: QRect(0, 0, margin, margin),
            Qt.TopRightCorner: QRect(max(0, width - margin), 0, margin, margin),
            Qt.BottomLeftCorner: QRect(0, max(0, height - margin), margin, margin),
            Qt.BottomRightCorner: QRect(max(0, width - margin), max(0, height - margin), margin, margin),
        }
        for handle in self._resize_handles:
            handle.setGeometry(geometries[handle._edges])
            handle.raise_()

    def _begin_resize(self, edges: Qt.Edge | Qt.Corner, global_pos: QPoint) -> None:
        self._resize_edges = edges
        self._resize_origin = global_pos
        self._resize_start_geometry = self.geometry()

    def _handle_resize_move(self, global_pos: QPoint) -> None:
        if self._resize_edges is None or self._resize_origin is None or self._resize_start_geometry is None:
            return

        delta = global_pos - self._resize_origin
        start = self._resize_start_geometry
        min_width = self.minimumWidth()
        min_height = self.minimumHeight()

        left = start.left()
        top = start.top()
        right = start.right()
        bottom = start.bottom()

        if self._resize_edges in (Qt.LeftEdge, Qt.TopLeftCorner, Qt.BottomLeftCorner):
            new_left = left + delta.x()
            max_left = right - min_width + 1
            left = min(new_left, max_left)
        if self._resize_edges in (Qt.RightEdge, Qt.TopRightCorner, Qt.BottomRightCorner):
            new_right = right + delta.x()
            min_right = left + min_width - 1
            right = max(new_right, min_right)
        if self._resize_edges in (Qt.TopEdge, Qt.TopLeftCorner, Qt.TopRightCorner):
            new_top = top + delta.y()
            max_top = bottom - min_height + 1
            top = min(new_top, max_top)
        if self._resize_edges in (Qt.BottomEdge, Qt.BottomLeftCorner, Qt.BottomRightCorner):
            new_bottom = bottom + delta.y()
            min_bottom = top + min_height - 1
            bottom = max(new_bottom, min_bottom)

        self.setGeometry(QRect(QPoint(left, top), QPoint(right, bottom)))

    def _end_resize(self) -> None:
        if self.assistant_card.isVisible():
            self._expanded_height = self.height()
        else:
            self._compact_height = self.height()
        self._resize_edges = None
        self._resize_origin = None
        self._resize_start_geometry = None

    def _header_rect(self) -> QRect:
        top_left = self.header_bar.mapTo(self, QPoint(0, 0))
        return QRect(top_left, self.header_bar.size())

    def _refresh_status_label(self) -> None:
        available_width = max(120, self.status_label.width() or 320)
        metrics = self.status_label.fontMetrics()
        elided = metrics.elidedText(
            self._status_full_text,
            Qt.ElideRight,
            available_width,
        )
        self.status_label.setText(elided)
        self.status_label.setToolTip(self._status_full_text)

    @staticmethod
    def _make_chip_button(
        text: str,
        *,
        primary: bool = False,
        danger: bool = False,
    ) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName("PrimaryChip")
        elif danger:
            button.setObjectName("DangerChip")
        return button

    @staticmethod
    def _make_window_button(text: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setAutoRaise(False)
        return button

    @staticmethod
    def _make_browser(min_height: int = 0) -> QTextBrowser:
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(False)
        browser.setFrameShape(QFrame.NoFrame)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        browser.document().setDocumentMargin(0)
        if min_height:
            browser.setMinimumHeight(min_height)
        return browser

    @staticmethod
    def _wrap_html(body: str) -> str:
        return f"""
        <html>
        <head>
            <style>
                body {{
                    margin: 0;
                    color: #f6f8fb;
                    font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", "Helvetica Neue", sans-serif;
                    font-size: 13px;
                    font-weight: 400;
                }}
                p, div, span {{
                    background: transparent;
                    font-weight: 400;
                    box-shadow: none;
                }}
                .placeholder {{
                    color: rgba(246, 248, 251, 126);
                    line-height: 1.34;
                    padding: 4px 0;
                }}
                .entry {{
                    padding: 0 0 6px 0;
                    background: transparent;
                    border: none;
                }}
                .timestamp {{
                    color: rgba(255, 255, 255, 89);
                    font-size: 11px;
                    line-height: 1.2;
                    margin: 0 0 2px 0;
                }}
                .english {{
                    color: rgba(255, 255, 255, 235);
                    font-size: 17px;
                    line-height: 1.28;
                    margin: 0;
                    font-weight: 400;
                }}
                .chinese {{
                    color: rgba(180, 200, 220, 217);
                    font-size: 14px;
                    line-height: 1.18;
                    margin: 2px 0 0 0;
                    font-weight: 300;
                }}
                .live-entry {{
                    padding-bottom: 0;
                }}
                .live-line {{
                    margin: 0;
                    padding: 0;
                }}
                .live-badge {{
                    display: inline-block;
                    margin-right: 6px;
                    padding: 1px 7px;
                    border-radius: 999px;
                    background: rgba(92, 150, 255, 40);
                    border: 1px solid rgba(92, 150, 255, 60);
                    color: rgba(220, 234, 255, 220);
                    font-size: 10px;
                    line-height: 1.2;
                    vertical-align: baseline;
                }}
                .live-english {{
                    display: inline;
                }}
                .assistant-copy {{
                    color: rgba(244, 247, 252, 220);
                    line-height: 1.5;
                    white-space: pre-wrap;
                }}
            </style>
        </head>
        <body>{body}</body>
        </html>
        """

    @staticmethod
    def _multiline_to_html(text: str) -> str:
        return html.escape(text).replace("\n", "<br>")

    @staticmethod
    def _scroll_to_bottom(browser: QTextBrowser) -> None:
        bar = browser.verticalScrollBar()
        bar.setValue(bar.maximum())


def run_app(controller: MeetingAssistantController) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Meeting Copilot")
    window = MainWindow(controller)
    window.show()
    window.place_default()
    return app.exec()
