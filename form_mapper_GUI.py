import sys
import csv
import os
from dataclasses import dataclass
from typing import List

import fitz  # PyMuPDF

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsRectItem,
    QGraphicsLineItem,
    QGraphicsTextItem,
    QPushButton,
    QLabel,
    QSpinBox,
    QGraphicsItem,
    QGraphicsDropShadowEffect,
)
from PySide6.QtGui import QPixmap, QImage, QPen, QColor, QBrush
from PySide6.QtCore import Qt, QRectF, QPointF


# Padding around the PDF so pills are always in-frame
MARGIN_X = 350
MARGIN_Y = 200


@dataclass
class FieldRow:
    row: int
    heading: str
    subheading: str
    form_entry_description: str
    x1: float
    y1: float
    x2: float
    y2: float
    page: int
    rich_description: str


class LabelPill(QGraphicsRectItem):
    """
    Rounded pill + text, movable as a unit.
    """
    def __init__(self, wrapper, field: FieldRow, pos: QPointF, text_width: float):
        super().__init__()
        self.wrapper = wrapper
        self.text_width = text_width
        self.padding_x = 12
        self.padding_y = 8
        self.gap = 6
        self.row_prefix = f"{field.row}:"

        # Prefix (non-editable) row number in pink
        self.prefix_item = QGraphicsTextItem(self)
        self.prefix_item.setHtml(
            f'<span style="color:#f9a8d4;font-weight:bold;">{self.row_prefix}</span>'
        )

        # Editable description text
        self.desc_item = QGraphicsTextItem(self)
        self.desc_item.setPlainText(field.rich_description)
        self.desc_item.setDefaultTextColor(QColor(15, 35, 80))
        self.desc_item.setTextWidth(text_width)
        self.desc_item.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.desc_item.setFlag(QGraphicsItem.ItemIsFocusable, True)
        self.desc_item.document().contentsChanged.connect(self.on_desc_changed)

        # Compute pill rect around combined text
        self.update_geometry()

        # Position pill in scene
        self.setPos(pos)

        # Style
        self.setBrush(QBrush(QColor(255, 255, 255, 235)))
        self.setPen(QPen(QColor(180, 200, 230), 1.5))

        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsScenePositionChanges, True)
        self.setZValue(10)

    def update_geometry(self):
        prefix_rect = self.prefix_item.boundingRect()
        self.prefix_item.setPos(0, 0)

        # Position description after prefix
        self.desc_item.setPos(prefix_rect.width() + self.gap, 0)
        self.desc_item.setTextWidth(self.text_width)
        desc_rect = self.desc_item.boundingRect()

        content_width = prefix_rect.width() + self.gap + desc_rect.width()
        content_height = max(prefix_rect.height(), desc_rect.height())

        self.setRect(
            -self.padding_x,
            -self.padding_y,
            content_width + 2 * self.padding_x,
            content_height + 2 * self.padding_y,
        )

    def description_text(self) -> str:
        return self.desc_item.toPlainText()

    def on_desc_changed(self):
        self.update_geometry()
        if self.wrapper is not None:
            self.wrapper.update_line()

    def paint(self, painter, option, widget=None):
        painter.setBrush(self.brush())
        painter.setPen(self.pen())
        painter.drawRoundedRect(self.rect(), 16, 16)

    def itemChange(self, change, value):
        # When pill moves, update line
        if change == QGraphicsItem.ItemPositionChange and self.wrapper is not None:
            self.wrapper.update_line()
        return super().itemChange(change, value)


class DraggableRectItem(QGraphicsRectItem):
    """
    Draggable field box on the PDF; moves arrow head and updates coords.
    """
    def __init__(self, wrapper, rect: QRectF, color: QColor):
        super().__init__(rect)
        self.wrapper = wrapper
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsScenePositionChanges, True)

        pen = QPen(color, 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 40)))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.wrapper is not None:
            self.wrapper.update_from_scene()
        return super().itemChange(change, value)


class FieldGraphics:
    """
    Container tying together: label pill, box rect, and line between them.
    """
    def __init__(
        self,
        scene: QGraphicsScene,
        field: FieldRow,
        label_pos: QPointF,
        label_width: float,
        scale_x: float,
        scale_y: float,
        color: QColor,
    ):
        self.scene = scene
        self.field = field
        self.scale_x = scale_x
        self.scale_y = scale_y

        # Movable pill + text
        self.label_pill = LabelPill(self, field, label_pos, label_width)
        scene.addItem(self.label_pill)

        # Box from PDF coords
        x1_scene = field.x1 * scale_x
        y1_scene = field.y1 * scale_y
        x2_scene = field.x2 * scale_x
        y2_scene = field.y2 * scale_y
        rect = QRectF(x1_scene, y1_scene, x2_scene - x1_scene, y2_scene - y1_scene)

        self.rect_item = DraggableRectItem(self, rect, color)
        scene.addItem(self.rect_item)

        # Line with glow
        self.line_item = QGraphicsLineItem()
        pen = QPen(color, 3.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        self.line_item.setPen(pen)

        effect = QGraphicsDropShadowEffect()
        effect.setBlurRadius(6)
        effect.setOffset(0, 0)
        glow_color = QColor(255, 255, 255, 180)
        effect.setColor(glow_color)
        self.line_item.setGraphicsEffect(effect)

        scene.addItem(self.line_item)

        self.update_line()

    def label_center(self) -> QPointF:
        return self.label_pill.mapToScene(self.label_pill.rect().center())

    def box_center(self) -> QPointF:
        return self.rect_item.mapToScene(self.rect_item.rect().center())

    def update_line(self):
        tail = self.label_center()
        head = self.box_center()
        self.line_item.setLine(tail.x(), tail.y(), head.x(), head.y())

    def update_from_scene(self):
        # Called when the box moves
        self.update_line()
        scene_rect = self.rect_item.sceneBoundingRect()
        x1_scene = scene_rect.left()
        y1_scene = scene_rect.top()
        x2_scene = scene_rect.right()
        y2_scene = scene_rect.bottom()
        self.field.x1 = x1_scene / self.scale_x
        self.field.y1 = y1_scene / self.scale_y
        self.field.x2 = x2_scene / self.scale_x
        self.field.y2 = y2_scene / self.scale_y

    def sync_field_data(self):
        self.update_from_scene()
        self.field.rich_description = self.label_pill.description_text().strip()


class ZoomableGraphicsView(QGraphicsView):
    """
    Ctrl/⌘ + mouse wheel to zoom; plain wheel to scroll.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._zoom = 0

    def wheelEvent(self, event):
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            if event.angleDelta().y() > 0:
                factor = 1.25
                self._zoom += 1
            else:
                factor = 0.8
                self._zoom -= 1
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)


class FormMapperWindow(QMainWindow):
    def __init__(self, pdf_path: str, csv_path: str):
        super().__init__()
        self.setWindowTitle("Form Field Mapper")

        self.pdf_path = pdf_path
        self.csv_path = csv_path

        self.doc = fitz.open(pdf_path)
        self.fields: List[FieldRow] = self.load_csv(csv_path)

        self.scene = QGraphicsScene(self)
        self.view = ZoomableGraphicsView(self.scene)
        self.view.setRenderHints(self.view.renderHints())
        self.view.setBackgroundBrush(QBrush(QColor(10, 15, 35)))

        self.current_page_index = 0
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.field_graphics: List[FieldGraphics] = []
        self.current_zoom = 1.0

        self.init_ui()
        self.load_page(0)

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout()
        central.setLayout(main_layout)

        # Top bar
        top_bar = QHBoxLayout()
        main_layout.addLayout(top_bar)

        top_bar.addWidget(QLabel("Page:"))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(self.doc.page_count)
        self.page_spin.setValue(1)
        self.page_spin.valueChanged.connect(self.on_page_changed)
        top_bar.addWidget(self.page_spin)

        zoom_out_btn = QPushButton("-")
        zoom_out_btn.setFixedWidth(28)
        zoom_out_btn.clicked.connect(self.zoom_out)
        top_bar.addWidget(zoom_out_btn)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedWidth(28)
        zoom_in_btn.clicked.connect(self.zoom_in)
        top_bar.addWidget(zoom_in_btn)
        top_bar.addStretch()

        # Graphics view
        main_layout.addWidget(self.view)

        # Bottom bar
        bottom_bar = QHBoxLayout()
        main_layout.addLayout(bottom_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: white;")
        bottom_bar.addWidget(self.status_label)
        bottom_bar.addStretch()

        submit_button = QPushButton("Submit (save corrected CSV)")
        submit_button.setStyleSheet(
            """
            QPushButton {
                background-color: #3b82f6;
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            """
        )
        submit_button.clicked.connect(self.on_submit)
        bottom_bar.addWidget(submit_button)

    def load_csv(self, path: str) -> List[FieldRow]:
        fields: List[FieldRow] = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fields.append(
                    FieldRow(
                        row=int(row["row"]),
                        heading=row["heading"],
                        subheading=row["subheading"],
                        form_entry_description=row["form_entry_description"],
                        x1=float(row["x1"]),
                        y1=float(row["y1"]),
                        x2=float(row["x2"]),
                        y2=float(row["y2"]),
                        page=int(row["page"]),
                        rich_description=row["rich_description"],
                    )
                )
        return fields

    def on_page_changed(self, value: int):
        self.persist_current_page_state()
        self.load_page(value - 1)

    def load_page(self, page_index: int):
        self.current_page_index = page_index
        self.scene.clear()
        self.field_graphics.clear()
        self.reset_zoom()

        page = self.doc[page_index]

        zoom = 2.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        if pix.alpha:
            img_format = QImage.Format_RGBA8888
        else:
            img_format = QImage.Format_RGB888

        image = QImage(pix.samples, pix.width, pix.height, pix.stride, img_format).copy()
        pixmap = QPixmap.fromImage(image)

        bg_item = self.scene.addPixmap(pixmap)
        bg_item.setZValue(-100)

        # Scene rect with thick margins
        self.scene.setSceneRect(
            -MARGIN_X,
            -MARGIN_Y,
            pix.width + 2 * MARGIN_X,
            pix.height + 2 * MARGIN_Y,
        )

        page_rect = page.rect
        pdf_width = page_rect.width
        pdf_height = page_rect.height

        self.scale_x = pix.width / pdf_width
        self.scale_y = pix.height / pdf_height

        page_num = page_index + 1
        page_fields = [f for f in self.fields if f.page == page_num]

        # Left/right groups based on x center
        mid_x_pdf = pdf_width / 2.0
        left_fields = []
        right_fields = []
        for f in page_fields:
            cx = (f.x1 + f.x2) / 2.0
            (left_fields if cx < mid_x_pdf else right_fields).append(f)

        # Sort each group by vertical center to minimize crossings
        def center_y(fr: FieldRow) -> float:
            return (fr.y1 + fr.y2) / 2.0

        left_fields.sort(key=center_y)
        right_fields.sort(key=center_y)

        label_width = MARGIN_X - 80
        left_x = -MARGIN_X + 40
        right_x = pix.width + 40
        y_start = -MARGIN_Y + 40
        y_step = 55

        colors = [
            QColor(244, 114, 182),  # magenta-ish
            QColor(56, 189, 248),   # teal-ish
        ]

        # Left side labels
        for idx, field in enumerate(left_fields):
            label_pos = QPointF(left_x, y_start + idx * y_step)
            color = colors[idx % len(colors)]
            fg = FieldGraphics(
                scene=self.scene,
                field=field,
                label_pos=label_pos,
                label_width=label_width,
                scale_x=self.scale_x,
                scale_y=self.scale_y,
                color=color,
            )
            self.field_graphics.append(fg)

        # Right side labels
        for idx, field in enumerate(right_fields):
            label_pos = QPointF(right_x, y_start + idx * y_step)
            color = colors[idx % len(colors)]
            fg = FieldGraphics(
                scene=self.scene,
                field=field,
                label_pos=label_pos,
                label_width=label_width,
                scale_x=self.scale_x,
                scale_y=self.scale_y,
                color=color,
            )
            self.field_graphics.append(fg)

        self.status_label.setText(
            f"Loaded page {page_num} with {len(page_fields)} fields"
        )

    def zoom_in(self):
        factor = 1.25
        self.current_zoom *= factor
        self.view.scale(factor, factor)

    def zoom_out(self):
        factor = 0.8
        self.current_zoom *= factor
        self.view.scale(factor, factor)

    def reset_zoom(self):
        self.view.resetTransform()
        self.current_zoom = 1.0

    def persist_current_page_state(self):
        for fg in self.field_graphics:
            fg.sync_field_data()

    def on_submit(self):
        # Sync any moved boxes
        for fg in self.field_graphics:
            fg.sync_field_data()

        base, ext = os.path.splitext(self.csv_path)
        out_path = base + "_corrected.csv"

        fieldnames = [
            "row",
            "heading",
            "subheading",
            "form_entry_description",
            "x1",
            "y1",
            "x2",
            "y2",
            "page",
            "rich_description",
        ]

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for fr in sorted(self.fields, key=lambda r: r.row):
                writer.writerow(
                    {
                        "row": fr.row,
                        "heading": fr.heading,
                        "subheading": fr.subheading,
                        "form_entry_description": fr.form_entry_description,
                        "x1": fr.x1,
                        "y1": fr.y1,
                        "x2": fr.x2,
                        "y2": fr.y2,
                        "page": fr.page,
                        "rich_description": fr.rich_description,
                    }
                )

        self.status_label.setText(f"Saved corrected CSV -> {out_path}")
        QMessageBox.information(self, "Saved", f"Saved corrected CSV to:\n{out_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python form_mapper_gui.py path/to/form.pdf path/to/form_map_rich.csv")
        sys.exit(1)

    pdf_path = sys.argv[1]
    csv_path = sys.argv[2]

    app = QApplication(sys.argv)
    window = FormMapperWindow(pdf_path, csv_path)
    window.resize(1400, 900)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
