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


# ------------------------------------------------------------------ #
#  Resize handle (unchanged)
# ------------------------------------------------------------------ #

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


# ------------------------------------------------------------------ #
#  Main window
# ------------------------------------------------------------------ #

class MainWindow(QMainWindow):
    def __init__(self, controller: MeetingAssistantController) -> None:
        super().__init__()
        self._controller = controller
        self._segments_by_id: dict[str, TranscriptSegment] = {}
        self._segment_order: list[str] = []
        self._committed_groups: list[dict] = []
        self._history_html_cache = ""
        self._current_partial_english = ""
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
        self.resize(960, self._compact_height)
        self.setMinimumSize(700, 300)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._apply_window_flags(initial=True)
        self._build_ui()
        self._build_resize_handles()
        self._bind_signals()

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #

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

        # --- Header ---
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

        middle_wrap = QVBoxLayout()
        middle_wrap.setContentsMargins(0, 0, 0, 0)
        middle_wrap.setSpacing(4)

        self.language_chip = QLabel("English  ->  Chinese")
        self.language_chip.setObjectName("LanguageChip")
        self.status_label = QLabel(self._status_full_text)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(False)
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setAlignment(Qt.AlignHCenter)
        self.status_label.setMaximumWidth(320)

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

        # --- Subtitle area: bilingual table + live bar ---
        self.subtitle_card = QFrame()
        self.subtitle_card.setObjectName("SubtitleCard")
        subtitle_layout = QVBoxLayout(self.subtitle_card)
        subtitle_layout.setContentsMargins(0, 0, 0, 0)
        subtitle_layout.setSpacing(0)

        # Single scrollable view: committed rows + live row at bottom
        self.history_view = self._make_browser(min_height=120)
        subtitle_layout.addWidget(self.history_view, 1)

        shell_layout.addWidget(self.subtitle_card, 1)

        # --- Assistant card ---
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

        # --- Action bar ---
        action_bar = QFrame()
        action_bar.setObjectName("ActionBar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)

        self.question_button = self._make_chip_button("Question")
        self.summary_button = self._make_chip_button("Summary")
        self.save_button = self._make_chip_button("Save")
        self.clear_button = self._make_chip_button("Clear")

        for btn in (self.question_button, self.summary_button, self.save_button, self.clear_button):
            action_layout.addWidget(btn)

        action_layout.addStretch(1)
        grip = QSizeGrip(self.shell)
        grip.setFixedSize(14, 14)
        action_layout.addWidget(grip, 0, Qt.AlignRight | Qt.AlignBottom)
        shell_layout.addWidget(action_bar)

        self.setStyleSheet(_STYLESHEET)
        self._render_history()

    # ------------------------------------------------------------------ #
    #  Signal binding
    # ------------------------------------------------------------------ #

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
        self._controller.signals.assistant.connect(self._set_assistant_content)
        self._controller.signals.segment_changed.connect(self._upsert_segment)
        self._controller.signals.running.connect(self._toggle_running_state)
        self._controller.signals.error.connect(self._show_error)

    # ------------------------------------------------------------------ #
    #  Live partial — rendered as last row in the table
    # ------------------------------------------------------------------ #

    def _set_partial_english(self, text: str) -> None:
        self._current_partial_english = text.strip()
        self._render_history()

    # ------------------------------------------------------------------ #
    #  History zone — committed bilingual table
    # ------------------------------------------------------------------ #

    def _set_status_message(self, text: str) -> None:
        self._status_full_text = text.strip() or "准备就绪。点击 Start 开始。"
        self._refresh_status_label()

    def _upsert_segment(self, segment: TranscriptSegment) -> None:
        is_new = segment.item_id not in self._segments_by_id
        if is_new:
            self._segment_order.append(segment.item_id)
        self._segments_by_id[segment.item_id] = segment

        if is_new:
            self._add_segment_to_group(segment)

        self._render_history()

    def _add_segment_to_group(self, segment: TranscriptSegment) -> None:
        eng = segment.english.strip()
        if not eng:
            return

        if self._committed_groups:
            last = self._committed_groups[-1]
            if self._should_merge(last, segment):
                last["segment_ids"].append(segment.item_id)
                return

        self._committed_groups.append(
            {"timestamp": segment.timestamp, "segment_ids": [segment.item_id]}
        )

    def _should_merge(self, group: dict, segment: TranscriptSegment) -> bool:
        if len(group["segment_ids"]) >= 5:
            return False
        last_seg = self._segments_by_id.get(group["segment_ids"][-1])
        if last_seg:
            gap = (segment.timestamp - last_seg.timestamp).total_seconds()
            if gap > 5:
                return False
        total_eng = sum(
            len(self._segments_by_id[sid].english)
            for sid in group["segment_ids"]
            if sid in self._segments_by_id
        )
        if total_eng + len(segment.english.strip()) > 400:
            return False
        return True

    def _render_history(self) -> None:
        rows: list[str] = []
        for group in self._committed_groups:
            eng_parts: list[str] = []
            chn_parts: list[str] = []
            for sid in group["segment_ids"]:
                seg = self._segments_by_id.get(sid)
                if not seg:
                    continue
                e = seg.english.strip()
                c = (seg.chinese or "翻译中...").strip()
                if e:
                    eng_parts.append(e)
                if c:
                    chn_parts.append(c)

            if not eng_parts:
                continue

            english = self._join_eng(eng_parts)
            chinese = self._join_chn(chn_parts)
            stamp = group["timestamp"].strftime("%H:%M:%S")

            rows.append(
                "<tr>"
                f"<td class='eng-col' valign='top'>"
                f"<div class='timestamp'>{html.escape(stamp)}</div>"
                f"<div class='english'>{html.escape(english)}</div>"
                "</td>"
                f"<td class='chn-col' valign='top'>"
                f"<div class='chinese'>{html.escape(chinese)}</div>"
                "</td>"
                "</tr>"
            )

        # Append LIVE row for partial English
        live_eng = self._current_partial_english
        if live_eng and self._segment_order:
            latest = self._segments_by_id.get(self._segment_order[-1])
            if latest and live_eng == latest.english:
                live_eng = ""
        if live_eng:
            badge = (
                "<span style='"
                "display:inline-block; margin-right:6px; padding:1px 7px; "
                "border-radius:999px; background:rgba(92,150,255,40); "
                "border:1px solid rgba(92,150,255,60); color:rgba(220,234,255,220); "
                "font-size:10px; line-height:1.2; vertical-align:baseline;"
                "'>LIVE</span>"
            )
            rows.append(
                "<tr>"
                f"<td class='eng-col' valign='top'>"
                f"<div class='english'>{badge}{html.escape(live_eng)}</div>"
                "</td>"
                "<td class='chn-col' valign='top'></td>"
                "</tr>"
            )

        if rows:
            body = f"<table class='bilingual' cellpadding='0' cellspacing='0'>{''.join(rows)}</table>"
        else:
            body = (
                "<div class='placeholder'>"
                "左侧实时英文原文 | 右侧中文翻译<br>"
                "点击 Start 开始监听系统音频。"
                "</div>"
            )

        if body == self._history_html_cache:
            return
        self._history_html_cache = body

        sb = self.history_view.verticalScrollBar()
        was_at_bottom = sb.value() >= sb.maximum() - 30

        self.history_view.setUpdatesEnabled(False)
        self.history_view.setHtml(self._wrap_html(body))
        self.history_view.setUpdatesEnabled(True)

        if was_at_bottom:
            sb = self.history_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    @staticmethod
    def _join_eng(parts: list[str]) -> str:
        result = ""
        for p in parts:
            if not result:
                result = p
            elif result.endswith(("-", "/", "(")) or p.startswith((".", ",", ";", ":", "?", "!")):
                result += p
            else:
                result += " " + p
        return result

    @staticmethod
    def _join_chn(parts: list[str]) -> str:
        return "".join(parts)

    # ------------------------------------------------------------------ #
    #  Assistant card
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  UI state helpers
    # ------------------------------------------------------------------ #

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
        self._committed_groups.clear()
        self._history_html_cache = ""
        self._current_partial_english = ""
        self._hide_assistant()
        self.history_view.clear()
        self._controller.clear()
        self._render_history()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._controller.stop()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._layout_resize_handles()
        self._refresh_status_label()

    # ------------------------------------------------------------------ #
    #  Window drag
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Resize handles
    # ------------------------------------------------------------------ #

    def _build_resize_handles(self) -> None:
        edges = (
            Qt.TopEdge, Qt.BottomEdge, Qt.LeftEdge, Qt.RightEdge,
            Qt.TopLeftCorner, Qt.TopRightCorner, Qt.BottomLeftCorner, Qt.BottomRightCorner,
        )
        self._resize_handles = [ResizeHandle(self, e, self._resize_margin) for e in edges]
        self._layout_resize_handles()

    def _layout_resize_handles(self) -> None:
        w, h, m = self.width(), self.height(), self._resize_margin
        geometries = {
            Qt.TopEdge: QRect(m, 0, max(0, w - 2 * m), m),
            Qt.BottomEdge: QRect(m, max(0, h - m), max(0, w - 2 * m), m),
            Qt.LeftEdge: QRect(0, m, m, max(0, h - 2 * m)),
            Qt.RightEdge: QRect(max(0, w - m), m, m, max(0, h - 2 * m)),
            Qt.TopLeftCorner: QRect(0, 0, m, m),
            Qt.TopRightCorner: QRect(max(0, w - m), 0, m, m),
            Qt.BottomLeftCorner: QRect(0, max(0, h - m), m, m),
            Qt.BottomRightCorner: QRect(max(0, w - m), max(0, h - m), m, m),
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
        mw, mh = self.minimumWidth(), self.minimumHeight()
        left, top, right, bottom = start.left(), start.top(), start.right(), start.bottom()
        if self._resize_edges in (Qt.LeftEdge, Qt.TopLeftCorner, Qt.BottomLeftCorner):
            left = min(left + delta.x(), right - mw + 1)
        if self._resize_edges in (Qt.RightEdge, Qt.TopRightCorner, Qt.BottomRightCorner):
            right = max(right + delta.x(), left + mw - 1)
        if self._resize_edges in (Qt.TopEdge, Qt.TopLeftCorner, Qt.TopRightCorner):
            top = min(top + delta.y(), bottom - mh + 1)
        if self._resize_edges in (Qt.BottomEdge, Qt.BottomLeftCorner, Qt.BottomRightCorner):
            bottom = max(bottom + delta.y(), top + mh - 1)
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
        elided = metrics.elidedText(self._status_full_text, Qt.ElideRight, available_width)
        self.status_label.setText(elided)
        self.status_label.setToolTip(self._status_full_text)

    # ------------------------------------------------------------------ #
    #  Factories
    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_chip_button(text: str, *, primary: bool = False, danger: bool = False) -> QPushButton:
        btn = QPushButton(text)
        if primary:
            btn.setObjectName("PrimaryChip")
        elif danger:
            btn.setObjectName("DangerChip")
        return btn

    @staticmethod
    def _make_window_button(text: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setAutoRaise(False)
        return btn

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
        <html><head><style>
        body {{
            margin: 0; color: #f6f8fb;
            font-family: "PingFang SC","Noto Sans SC","Microsoft YaHei","Helvetica Neue",sans-serif;
            font-size: 13px; font-weight: 400;
        }}
        p, div, span, td {{ background: transparent; font-weight: 400; box-shadow: none; }}
        .placeholder {{
            color: rgba(246,248,251,126); line-height: 1.4; padding: 12px 0;
            text-align: center;
        }}
        table.bilingual {{ width: 100%; border-collapse: collapse; }}
        table.bilingual td {{ padding: 4px 0; }}
        td.eng-col {{
            width: 50%; padding-right: 10px;
            border-right: 1px solid rgba(255,255,255,18);
        }}
        td.chn-col {{ width: 50%; padding-left: 10px; }}
        .timestamp {{
            color: rgba(255,255,255,80); font-size: 10px;
            line-height: 1.2; margin: 0 0 1px 0;
        }}
        .english {{
            color: rgba(255,255,255,230); font-size: 15px;
            line-height: 1.32; margin: 0; font-weight: 400;
        }}
        .chinese {{
            color: rgba(180,200,220,210); font-size: 14px;
            line-height: 1.28; margin: 0; font-weight: 300;
        }}
        .assistant-copy {{
            color: rgba(244,247,252,220); line-height: 1.5; white-space: pre-wrap;
        }}
        </style></head><body>{body}</body></html>
        """

    @staticmethod
    def _multiline_to_html(text: str) -> str:
        return html.escape(text).replace("\n", "<br>")

    @staticmethod
    def _scroll_to_bottom(browser: QTextBrowser) -> None:
        bar = browser.verticalScrollBar()
        bar.setValue(bar.maximum())


# ------------------------------------------------------------------ #
#  Stylesheet (extracted to module level for readability)
# ------------------------------------------------------------------ #

_STYLESHEET = """
QWidget#Root {
    background: transparent; color: #f5f7fb;
    font-family: "PingFang SC","Noto Sans SC","Microsoft YaHei","Helvetica Neue",sans-serif;
    font-size: 13px;
}
QFrame#GlassShell {
    background: rgba(20,24,30,224);
    border: 1px solid rgba(255,255,255,20); border-radius: 14px;
}
QFrame#SubtitleCard { background: transparent; border: none; }
QFrame#AssistantCard {
    background: rgba(255,255,255,12);
    border: 1px solid rgba(255,255,255,16); border-radius: 12px;
}
QLabel#StatusChip, QLabel#LanguageChip {
    background: rgba(255,255,255,16); color: rgba(247,250,255,210);
    border: 1px solid rgba(255,255,255,24); border-radius: 12px;
    padding: 6px 12px; font-weight: 600;
}
QLabel#StatusLabel {
    color: rgba(245,249,255,168); font-size: 12px; font-weight: 400;
}
QLabel#AssistantTitle { color: #f8fbff; font-size: 13px; font-weight: 600; }
QPushButton {
    background: rgba(255,255,255,16); color: #f6f8fb;
    border: 1px solid rgba(255,255,255,24); border-radius: 16px;
    padding: 8px 14px; font-weight: 600;
}
QPushButton:hover { background: rgba(255,255,255,24); }
QPushButton:disabled { background: rgba(255,255,255,10); color: rgba(246,248,251,96); }
QPushButton#PrimaryChip {
    background: rgba(96,162,255,44); border: 1px solid rgba(96,162,255,90);
}
QPushButton#DangerChip {
    background: rgba(255,92,92,44); border: 1px solid rgba(255,92,92,90);
}
QToolButton {
    background: rgba(255,255,255,14); color: rgba(247,250,255,214);
    border: 1px solid rgba(255,255,255,22); border-radius: 14px;
    padding: 7px 12px; font-weight: 600;
}
QToolButton:hover { background: rgba(255,255,255,24); }
QToolButton#PinButton:checked {
    background: rgba(120,182,255,44); border: 1px solid rgba(120,182,255,90);
}
QToolButton#CloseButton:hover {
    background: rgba(255,92,92,54); border: 1px solid rgba(255,92,92,110);
}
QTextBrowser {
    background: transparent; color: #f5f7fb; border: none; padding: 0px;
    font-family: "PingFang SC","Noto Sans SC","Microsoft YaHei","Helvetica Neue",sans-serif;
    selection-background-color: rgba(114,171,255,115);
}
QScrollBar:vertical {
    background: transparent; width: 8px; margin: 2px 0 2px 0;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,40); border-radius: 4px; min-height: 18px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent; border: none; height: 0px;
}
QSizeGrip { width: 14px; height: 14px; }
"""


def run_app(controller: MeetingAssistantController) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Meeting Copilot")
    window = MainWindow(controller)
    window.show()
    window.place_default()
    return app.exec()
