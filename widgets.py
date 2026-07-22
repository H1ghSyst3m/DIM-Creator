import os
import shutil
import tempfile
import base64
import uuid

from PySide6.QtWidgets import (
    QMessageBox, QWidget, QLabel, QDialog, QVBoxLayout, QFileDialog,
    QHBoxLayout, QFileSystemModel, QListWidget, QListWidgetItem, QCheckBox
)
from PySide6.QtCore import (
    QByteArray, QBuffer, QIODevice, Qt, QThread, Signal, QEasingCurve,
    QUrl, QTimer
)
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply
)
from PySide6.QtGui import (
    QPixmap, QImage, QImageReader, QCursor, QDesktopServices,
    QKeySequence, QShortcut
)
from qfluentwidgets import (
    setTheme, Theme, PrimaryPushButton, PushButton, Action, RoundMenu, LineEdit,
    InfoBar, InfoBarPosition, InfoBarIcon,
    CompactSpinBox, TogglePushButton, FlowLayout, TreeView,
    MessageBoxBase, SubtitleLabel, ToolButton
)
from qfluentwidgets import FluentIcon as FIF

from utils import (
    COVERS_DIR, canonical_path, clean_build_content, get_build_content_dir,
    has_reparse_component, has_reparse_point, is_path_within, move_to_trash, resource_path,
    safe_child_path, show_error, show_info, show_warning,
    validate_windows_name,
)
from build_manager import validate_build, get_build_data, reorder_builds
from logger_utils import get_logger

CHECKBOX_CHECKMARK_SVG = """
<svg width="16" height="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
<path d="M13 4L6 11L3 8" stroke="white" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
""".strip()

CHECKBOX_CHECKMARK_BASE64 = base64.b64encode(CHECKBOX_CHECKMARK_SVG.encode('utf-8')).decode('ascii')


log = get_logger(__name__)


class ProductLineEdit(LineEdit):
    def __init__(self, parent=None):
        super(ProductLineEdit, self).__init__(parent)
        self.textChanged.connect(self.onTextChanged)

    def keyPressEvent(self, event):
        forbidden_chars = set('/\\:*?"<>|')
        if event.text() in forbidden_chars:
            event.ignore()
            return
        super().keyPressEvent(event)

    def onTextChanged(self, text):
        forbidden_chars = set('/\\:*?"<>|')
        filtered_text = ''.join(ch for ch in text if ch not in forbidden_chars)

        if text != filtered_text:
            self.blockSignals(True)
            self.setText(filtered_text)
            self.blockSignals(False)


class TagSelectionDialog(QDialog):
    def __init__(self, available_tags, selected_tags=None, parent=None):
        super().__init__(parent, Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle("Select Tags")
        self.setStyleSheet("TagSelectionDialog{background: rgb(32, 32, 32)}")
        setTheme(Theme.DARK)
        self.resize(450, 370)

        if selected_tags is None:
            selected_tags = []
        self.selected_tags = selected_tags

        self.layout = QVBoxLayout(self)

        self.tag_buttons_container = QWidget(self)
        self.tags_layout = FlowLayout(self.tag_buttons_container, needAni=True)
        self.tags_layout.setAnimation(250, QEasingCurve.OutQuad)
        self.tags_layout.setContentsMargins(10, 10, 10, 10)
        self.tags_layout.setVerticalSpacing(10)
        self.tags_layout.setHorizontalSpacing(10)

        self.initUI(available_tags)

        buttonLayout = QHBoxLayout()
        buttonLayout.addStretch(1)

        self.okButton = PushButton('OK', self)
        self.okButton.clicked.connect(self.accept)
        buttonLayout.addWidget(self.okButton)

        self.cancelButton = PushButton('Cancel', self)
        self.cancelButton.clicked.connect(self.reject)
        buttonLayout.addWidget(self.cancelButton)

        self.layout.addLayout(buttonLayout)

    def initUI(self, available_tags):
        for tag in available_tags:
            tag_button = TogglePushButton(tag, self.tag_buttons_container)
            tag_button.setCheckable(True)
            tag_button.setChecked(tag in self.selected_tags)
            self.tags_layout.addWidget(tag_button)

        self.layout.addWidget(self.tag_buttons_container)

    def getSelectedTags(self):
        selected_tags = []
        for i in range(self.tags_layout.count()):
            widget = self.tags_layout.itemAt(i).widget()
            if widget.isChecked():
                selected_tags.append(widget.text())
        return selected_tags


class CustomCompactSpinBox(CompactSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)

    def textFromValue(self, value):
        return f"{value:02d}"


class ImageLabel(QLabel):
    imageChanged = Signal(str)

    MAX_IMAGE_BYTES = 20 * 1024 * 1024
    MAX_IMAGE_PIXELS = 40_000_000
    DOWNLOAD_TIMEOUT_MS = 15_000
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.imagePath = ""
        self._ownedTemp = False
        self.defaultText = "Drop Image Here\nOr Click to Select"
        self.placeholder_image_rel = os.path.join('assets', 'images', 'placeholder', 'imageexport.png')
        self.placeholder_max_px = 96
        self._load_seq = 0
        self._active_reply = None
        self._download_timer = QTimer(self)
        self._download_timer.setSingleShot(True)
        self._download_timer.timeout.connect(self._abort_active_download)

        self._is_placeholder = True
        self._orig_pixmap = None

        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            'border: 2px solid #323232; border-radius: 5px; '
            'color: white; font-family: "Segoe UI"; font-size: 10pt;'
        )
        self.originalStyleSheet = self.styleSheet()

        self.removeImageButton = PrimaryPushButton(FIF.CLOSE, "Remove", self)
        self.removeImageButton.clicked.connect(self.removeImage)
        self.removeImageButton.hide()

        self.resetToPlaceholder()

        self._nam = QNetworkAccessManager(self)

    def _scaled_for_placeholder(self, pm: QPixmap) -> QPixmap:
        if pm.isNull():
            return pm
        target = min(self.placeholder_max_px, max(1, min(self.width(), self.height())))
        if pm.width() > target or pm.height() > target:
            pm = pm.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pm

    def _scaled_for_content(self, pm: QPixmap) -> QPixmap:
        if pm.isNull():
            return pm
        w, h = max(1, self.width()), max(1, self.height())
        if pm.width() > w or pm.height() > h:
            pm = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pm

    def _apply_scaled_pixmap(self):
        if not self._orig_pixmap:
            return
        if self._is_placeholder:
            pm = self._scaled_for_placeholder(self._orig_pixmap)
        else:
            pm = self._scaled_for_content(self._orig_pixmap)
        if not pm.isNull():
            self.setPixmap(pm)
        else:
            self.setText(self.defaultText)

    def resizeEvent(self, event):
        self._apply_scaled_pixmap()
        self.updateButtonPosition()
        super().resizeEvent(event)

    def loadPlaceholderImage(self):
        path = resource_path(self.placeholder_image_rel)
        if os.path.exists(path):
            pm = QPixmap(path)
            self._orig_pixmap = pm
            self._is_placeholder = True
            self._apply_scaled_pixmap()
        else:
            self._orig_pixmap = None
            self.setText(self.defaultText)
        self.imagePath = ""
        self._ownedTemp = False
        self.removeImageButton.hide()

    def resetToPlaceholder(self, *, emit=True):
        self._abort_active_download()
        self.loadPlaceholderImage()
        if emit:
            self.imageChanged.emit("")

    def _read_image_file(self, path: str) -> QImage:
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            return QImage()
        if not os.path.isfile(path) or size_bytes > self.MAX_IMAGE_BYTES:
            return QImage()
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if (not size.isValid() or size.width() * size.height() > self.MAX_IMAGE_PIXELS):
            return QImage()
        return reader.read()

    def _read_image_bytes(self, data: bytes) -> QImage:
        if not data or len(data) > self.MAX_IMAGE_BYTES:
            return QImage()
        buffer = QBuffer(self)
        buffer.setData(QByteArray(data))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            return QImage()
        reader = QImageReader(buffer)
        reader.setDecideFormatFromContent(True)
        reader.setAutoTransform(True)
        size = reader.size()
        if (not size.isValid() or size.width() * size.height() > self.MAX_IMAGE_PIXELS):
            return QImage()
        return reader.read()

    def _persist_image(self, image: QImage) -> str:
        if image.isNull() or image.width() * image.height() > self.MAX_IMAGE_PIXELS:
            raise ValueError("Image is invalid or exceeds 40 megapixels")
        os.makedirs(COVERS_DIR, exist_ok=True)
        final_path = os.path.join(COVERS_DIR, f"cover-{uuid.uuid4().hex}.jpg")
        fd, temp_path = tempfile.mkstemp(prefix=".cover-", suffix=".tmp", dir=COVERS_DIR)
        os.close(fd)
        try:
            if not image.convertToFormat(QImage.Format.Format_RGB888).save(
                    temp_path, "JPEG", 92):
                raise OSError("Could not encode cover as JPEG")
            if os.path.getsize(temp_path) > self.MAX_IMAGE_BYTES:
                raise ValueError("Normalized cover exceeds 20 MiB")
            os.replace(temp_path, final_path)
            return final_path
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _show_image(self, image: QImage, path: str, *, emit=True):
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self.resetToPlaceholder(emit=emit)
            return
        self.imagePath = path
        self._ownedTemp = False
        self._orig_pixmap = pixmap
        self._is_placeholder = False
        self._apply_scaled_pixmap()
        self.removeImageButton.show()
        self.updateButtonPosition()
        if emit:
            self.imageChanged.emit(path)

    def setImagePath(self, path):
        if not path:
            self.resetToPlaceholder()
            return
        image = self._read_image_file(path)
        if image.isNull():
            self.resetToPlaceholder()
            return
        self._abort_active_download()
        managed_path = path
        if not is_path_within(path, COVERS_DIR) or has_reparse_point(path):
            try:
                managed_path = self._persist_image(image)
            except (OSError, ValueError) as exc:
                log.warning("Could not persist cover '%s': %s", path, exc)
                self.resetToPlaceholder()
                return
        self._show_image(image, managed_path)

    def removeImage(self):
        self.resetToPlaceholder()

    def updateButtonPosition(self):
        buttonSize = self.removeImageButton.sizeHint()
        self.removeImageButton.move(self.width() - buttonSize.width() - 10,
                                    self.height() - buttonSize.height() - 10)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasImage():
            event.acceptProposedAction()
            return
        if md.hasUrls():
            url = md.urls()[0]
            if url.isLocalFile():
                fp = url.toLocalFile().lower()
                if fp.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.webp')):
                    event.acceptProposedAction()
                    return
            else:
                scheme = url.scheme().lower()
                if scheme in ('http', 'https', 'data'):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        md = event.mimeData()
        handled = False

        if md.hasImage():
            qimg = md.imageData()
            if isinstance(qimg, QPixmap) and not qimg.isNull():
                self._adopt_qimage_as_temp(qimg.toImage())
                handled = True
            elif isinstance(qimg, QImage) and not qimg.isNull():
                self._adopt_qimage_as_temp(qimg)
                handled = True

        if not handled and md.hasUrls():
            urls = md.urls()

            data_urls = [u for u in urls if u.scheme().lower() == 'data']
            local_urls = [u for u in urls if u.isLocalFile()]
            http_urls = [u for u in urls if u.scheme().lower() in ('http', 'https')]

            for u in data_urls:
                if self._adopt_data_url(u):
                    handled = True
                    break

            if not handled:
                for u in local_urls:
                    if self._adopt_local_as_temp(u.toLocalFile()):
                        handled = True
                        break

            if not handled and http_urls:
                self._load_seq += 1
                seq = self._load_seq
                self._download_first_valid(http_urls, seq)
                handled = True

        if handled:
            event.acceptProposedAction()
        else:
            event.ignore()

    def mousePressEvent(self, event):
        filePath, _ = QFileDialog.getOpenFileName(
            self, "Select an image", "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if filePath:
            self._adopt_local_as_temp(filePath)

    def enterEvent(self, event):
        self.setStyleSheet(
            'border: 2px solid #25d9e6; border-radius: 5px; '
            'color: white; font-family: "Segoe UI"; font-size: 10pt;'
        )
        self.setCursor(QCursor(Qt.PointingHandCursor))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self.originalStyleSheet)
        self.setCursor(QCursor(Qt.ArrowCursor))
        super().leaveEvent(event)

    def _download_first_valid(self, urls, seq):
        if not urls:
            self._active_reply = None
            return

        self._abort_active_download(invalidate=False)
        url = urls[0]
        req = QNetworkRequest(url)
        req.setTransferTimeout(self.DOWNLOAD_TIMEOUT_MS)
        reply = self._nam.get(req)
        self._active_reply = reply
        self._download_timer.start(self.DOWNLOAD_TIMEOUT_MS)

        length = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
        if length is not None and int(length) > self.MAX_IMAGE_BYTES:
            reply.abort()

        def _progress(received, _total):
            if received > self.MAX_IMAGE_BYTES:
                reply.abort()

        reply.downloadProgress.connect(_progress)

        def _finished():
            self._download_timer.stop()
            if self._active_reply is reply:
                self._active_reply = None
            if seq != self._load_seq:
                reply.deleteLater()
                return

            try:
                if reply.error() != QNetworkReply.NoError:
                    reply.deleteLater()
                    self._download_first_valid(urls[1:], seq)
                    return

                data = bytes(reply.readAll())
                image = self._read_image_bytes(data)
                if image.isNull():
                    reply.deleteLater()
                    self._download_first_valid(urls[1:], seq)
                    return
                if seq == self._load_seq:
                    try:
                        self._show_image(image, self._persist_image(image))
                    except (OSError, ValueError) as exc:
                        log.warning("Could not persist downloaded cover: %s", exc)
                        self._download_first_valid(urls[1:], seq)

            finally:
                reply.deleteLater()

        reply.finished.connect(_finished)

    def _abort_active_download(self, *, invalidate=True):
        if invalidate:
            self._load_seq += 1
        reply = self._active_reply
        self._active_reply = None
        self._download_timer.stop()
        if reply is not None and not reply.isFinished():
            reply.abort()

    def _adopt_qimage_as_temp(self, qimg: QImage, suffix=".png"):
        try:
            self._abort_active_download()
            self._show_image(qimg, self._persist_image(qimg))
            return True
        except (OSError, ValueError):
            return False

    def _adopt_local_as_temp(self, src_path: str):
        try:
            self._abort_active_download()
            image = self._read_image_file(src_path)
            if image.isNull():
                raise ValueError("Invalid or oversized image")
            self._show_image(image, self._persist_image(image))
            return True
        except (OSError, ValueError):
            return False

    def _set_owned_temp_path(self, temp_path: str):
        self._adopt_local_as_temp(temp_path)

    def _download_url_to_temp(self, url: QUrl):
        self._load_seq += 1
        self._download_first_valid([url], self._load_seq)

    def _adopt_data_url(self, url: QUrl) -> bool:
        try:
            s = url.toString()
            if not s.startswith('data:image/'):
                return False
            if len(s) > (self.MAX_IMAGE_BYTES * 4 // 3) + 4096:
                return False

            header, data = s.split(',', 1)

            header_parts = header.split(';')
            mime = header_parts[0][5:]
            mime_main, _, mime_sub = mime.partition('/')

            unsupported = {'image/svg+xml', 'image/heic', 'image/heif', 'image/tiff'}
            if mime.lower() in unsupported:
                return False

            ext_map = {
                'png': '.png', 'jpeg': '.jpg', 'jpg': '.jpg', 'bmp': '.bmp', 'webp': '.webp', 'gif': '.gif',
                'x-xbitmap': '.xbm', 'x-xpixmap': '.xpm', 'pbm': '.pbm', 'pgm': '.pgm', 'ppm': '.ppm'
            }
            ext = ext_map.get(mime_sub.lower(), '.png')

            is_base64 = any(part.strip().lower() == 'base64' for part in header_parts[1:])

            if is_base64:
                b = data.strip()
                pad = len(b) % 4
                if pad:
                    b += '=' * (4 - pad)
                raw = base64.b64decode(b, validate=True)
            else:
                raw = QUrl.fromPercentEncoding(data.encode('utf-8'))
                if not isinstance(raw, (bytes, bytearray)):
                    raw = bytes(raw)

            image = self._read_image_bytes(bytes(raw))
            if image.isNull():
                return False
            self._abort_active_download()
            self._show_image(image, self._persist_image(image))
            return True
        except (OSError, ValueError, TypeError, base64.binascii.Error):
            return False


class NameEntryDialog(MessageBoxBase):
    def __init__(self, parent=None, title="Enter Name", placeholder="Enter name here"):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.nameLineEdit = LineEdit(self)
        self.nameLineEdit.setPlaceholderText(placeholder)
        self.nameLineEdit.setClearButtonEnabled(True)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.nameLineEdit)

        self.yesButton.setText('OK')
        self.cancelButton.setText('Cancel')

        self.widget.setMinimumWidth(350)
        self.yesButton.setDisabled(True)
        self.nameLineEdit.textChanged.connect(self._validateName)

    def _validateName(self, text):
        try:
            validate_windows_name(text)
            valid = True
        except ValueError:
            valid = False
        self.yesButton.setEnabled(valid)

    def getName(self):
        return self.nameLineEdit.text().strip()


class CustomTreeView(TreeView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.internalDrag = False
        self.overwrite_all = False
        self.setDragDropMode(TreeView.DragDropMode.DragDrop)

    def startDrag(self, supportedActions):
        self.internalDrag = True
        super().startDrag(supportedActions)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            if self.internalDrag:
                event.setDropAction(Qt.MoveAction)
            else:
                event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            if self.internalDrag:
                event.setDropAction(Qt.MoveAction)
            else:
                event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        self.internalDrag = False
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        file_explorer = self.parent()
        if not file_explorer._mutation_allowed():
            event.ignore()
            return

        destinationIndex = self.indexAt(event.pos())
        main_gui = getattr(file_explorer, "main_gui", None)
        if main_gui is not None and hasattr(main_gui, 'current_build') and main_gui.current_build:
            build_content_dir = get_build_content_dir(main_gui.current_build.folder)
            
            if hasattr(file_explorer, 'current_path'):
                if canonical_path(file_explorer.current_path) != canonical_path(build_content_dir):
                    log.warning(f"FileExplorer path mismatch: displaying {file_explorer.current_path} but current build is {build_content_dir}")
                    file_explorer.InvalidFolderInfoBar()
                    event.ignore()
                    return
        else:
            log.warning("No current build available for drag & drop")
            event.ignore()
            return
        
        destinationPath = (
            self.model().filePath(destinationIndex)
            if destinationIndex.isValid()
            else build_content_dir
        )

        is_inside = is_path_within(destinationPath, build_content_dir)

        if not is_inside:
            print(f"Attempt to drop outside build directory: {destinationPath} to {build_content_dir}")
            log.warning(f"Attempt to drop outside build directory: {destinationPath} to {build_content_dir}")
            self.parent().InvalidFolderInfoBar()
            event.ignore()
            return

        any_file_op = False

        for url in event.mimeData().urls():
            sourcePath = url.toLocalFile()
            try:
                if sourcePath.lower().endswith(('.zip', '.rar', '.7z')):
                    mg = getattr(self.parent(), "main_gui", None)
                    if mg:
                        worker = getattr(mg, "extractionWorker", None)
                        if worker and worker.isRunning():
                            show_info(mg, "Extraction running", "Please wait for the current extraction to finish.")
                        else:
                            mg.dropExtractArchive(sourcePath)
                else:
                    if event.source() == self:
                        self.movePath(sourcePath, destinationPath)
                    else:
                        self.copyPath(sourcePath, destinationPath)
                    any_file_op = True
            except Exception as e:
                print(f"Error moving/copying {sourcePath} to {destinationPath}: {e}")
                log.error(f"Error moving/copying {sourcePath} to {destinationPath}: {e}")
                self.parent().InvalidFolderInfoBar()

        self.overwrite_all = False

        if any_file_op:
            QTimer.singleShot(0, self.parent().refresh_view)

    def copyPath(self, sourcePath, destinationPath):
        return self.parent()._transfer_path(
            sourcePath, destinationPath, move=False,
            overwrite_all=self.overwrite_all,
            update_overwrite=lambda: setattr(self, "overwrite_all", True),
        )

    def movePath(self, sourcePath, destinationPath):
        return self.parent()._transfer_path(
            sourcePath, destinationPath, move=True,
            overwrite_all=self.overwrite_all,
            update_overwrite=lambda: setattr(self, "overwrite_all", True),
        )


class FileExplorer(QWidget):
    def __init__(self, path=None, parent=None, main_gui=None):
        super().__init__(parent)
        self.main_gui = main_gui

        self.clipboard = None
        self.isCutOperation = False

        self.current_path = os.path.abspath(path) if path else ""
        self._root_valid = self._root_is_safe(self.current_path)

        self.model = QFileSystemModel()
        self.model.setRootPath(self.current_path if self._root_valid else "")
        self.treeView = CustomTreeView(self)
        self.treeView.setModel(self.model)
        self.treeView.setExpandsOnDoubleClick(False)

        self.treeView.setSortingEnabled(True)
        self.treeView.sortByColumn(0, Qt.AscendingOrder)

        self.treeView.setAcceptDrops(True)
        self.treeView.setDragEnabled(True)
        self.treeView.setDragDropMode(TreeView.DragDropMode.DragDrop)

        specificIndex = self.model.index(self.current_path)
        self.treeView.setRootIndex(specificIndex)
        self.treeView.setVisible(self._root_valid)

        self.treeView.setColumnWidth(0, 360)
        self.treeView.setColumnWidth(1, 100)
        self.treeView.setColumnWidth(2, 120)
        self.treeView.setColumnWidth(3, 150)
        self.treeView.doubleClicked.connect(self.on_double_click)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.treeView)
        self.setLayout(layout)
        
        self.setupShortcuts()

    def on_double_click(self, index):
        try:
            path = self._checked_path(
                self.model.filePath(index), allow_root=True
            )
            if os.path.isdir(path):
                if self.treeView.isExpanded(index):
                    self.treeView.collapse(index)
                else:
                    self.treeView.expand(index)
            else:
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                    raise Exception(f"Failed to open file: {path}")
            QTimer.singleShot(0, self.refresh_view)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                str(e),
                QMessageBox.StandardButton.Ok
            )
            print(f"Error: {e}")
            log.error(f"Error: {e}")
            QTimer.singleShot(0, self.refresh_view)

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def InvalidFolderInfoBar(self):
        content = "An error has occurred. Please check out logs."
        w = InfoBar(
            icon=InfoBarIcon.INFORMATION,
            title='Invalid Destination',
            content=content,
            orient=Qt.Orientation.Vertical,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=2000,
            parent=self
        )
        w.show()

    def _mutation_allowed(self) -> bool:
        checker = getattr(self.main_gui, "canMutateWorkspace", None)
        if checker and not checker():
            show_info(self, "Busy", "Please wait for the current operation to finish.")
            return False
        root_checker = getattr(self, "_root_is_safe", None)
        if callable(root_checker) and not root_checker(self.current_path):
            self._root_valid = False
        if not getattr(self, "_root_valid", True):
            show_warning(self, "Invalid Content Folder", "The Content folder is unavailable.")
            return False
        current_build = getattr(self.main_gui, "current_build", None)
        if current_build is not None:
            expected_root = get_build_content_dir(current_build.folder)
            if canonical_path(self.current_path) != canonical_path(expected_root):
                log.warning(
                    "Blocked mutation for stale FileExplorer root: %s (expected %s)",
                    self.current_path, expected_root,
                )
                self.InvalidFolderInfoBar()
                return False
        return self.isEnabled()

    def _root_is_safe(self, path: str) -> bool:
        if not path or not os.path.isdir(path):
            return False
        candidate = os.path.abspath(path)
        current_build = getattr(self.main_gui, "current_build", None)
        if current_build is None:
            return False
        try:
            expected = os.path.abspath(
                get_build_content_dir(current_build.folder)
            )
        except (AttributeError, OSError, ValueError):
            return False
        if canonical_path(candidate) != canonical_path(expected):
            return False
        builds_root = os.path.dirname(os.path.dirname(expected))
        return (
            is_path_within(candidate, builds_root, allow_root=False)
            and not has_reparse_component(candidate, builds_root)
        )

    def _checked_path(self, path: str, *, allow_root=False,
                      must_exist=True) -> str:
        candidate = os.path.abspath(path)
        if not is_path_within(candidate, self.current_path, allow_root=allow_root):
            raise ValueError("Path is outside the current Content folder")
        if must_exist and not os.path.exists(candidate):
            raise ValueError("Path does not exist")
        if has_reparse_component(candidate, self.current_path):
            raise ValueError("Links and reparse points cannot be modified")
        return candidate

    @staticmethod
    def _remove_permanently(path: str):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

    @staticmethod
    def _validate_copy_source(path: str):
        if has_reparse_point(path):
            raise ValueError("Links and reparse points cannot be copied")
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for name in dirs + files:
                    if has_reparse_point(os.path.join(root, name)):
                        raise ValueError(
                            "Folders containing links or reparse points cannot be copied"
                        )

    def _transfer_path(self, source_path: str, destination_path: str, *,
                       move: bool, overwrite_all=False, update_overwrite=None):
        if not self._mutation_allowed():
            return False
        try:
            source = os.path.abspath(source_path)
            if not os.path.exists(source):
                raise ValueError("Source no longer exists")
            self._validate_copy_source(source)
            if move:
                source = self._checked_path(source, allow_root=False)

            destination = destination_path
            if not os.path.isdir(destination):
                destination = os.path.dirname(destination)
            destination = self._checked_path(destination, allow_root=True)
            basename = validate_windows_name(os.path.basename(source.rstrip(os.sep)))
            target = safe_child_path(self.current_path, destination, basename)

            if canonical_path(source) == canonical_path(target):
                return False
            if os.path.isdir(source) and is_path_within(
                    target, source, allow_root=False):
                raise ValueError("A folder cannot be copied into itself")

            if os.path.exists(target) and not overwrite_all:
                reply = QMessageBox.question(
                    self, "Item exists",
                    f"'{basename}' already exists. Replace it?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.YesToAll,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.No:
                    return False
                if (reply == QMessageBox.StandardButton.YesToAll
                        and update_overwrite is not None):
                    update_overwrite()

            token = uuid.uuid4().hex
            transaction_dir = tempfile.mkdtemp(
                prefix=".dimcreator-fileop-",
                dir=os.path.dirname(self.current_path),
            )
            stage = os.path.join(transaction_dir, f"{token}.stage")
            backup = os.path.join(transaction_dir, f"{token}.backup")
            try:
                if os.path.isdir(source):
                    shutil.copytree(source, stage)
                else:
                    shutil.copy2(source, stage)

                had_target = os.path.exists(target)
                if had_target:
                    os.replace(target, backup)
                try:
                    os.replace(stage, target)
                except Exception:
                    if had_target and os.path.exists(backup):
                        os.replace(backup, target)
                    raise

                if os.path.exists(backup):
                    try:
                        self._remove_permanently(backup)
                    except OSError as exc:
                        log.warning(
                            "Could not remove file-operation backup '%s': %s",
                            backup, exc,
                        )
                if move:
                    try:
                        self._remove_permanently(source)
                    except OSError as exc:
                        show_warning(
                            self, "Move Incomplete",
                            "The copy succeeded, but the original could not be removed: "
                            f"{exc}", Qt.Vertical,
                        )
                        return False
                log.info("%s '%s' to '%s'", "Moved" if move else "Copied",
                         source, target)
                return True
            finally:
                if os.path.exists(stage):
                    self._remove_permanently(stage)
                try:
                    os.rmdir(transaction_dir)
                except OSError as exc:
                    log.warning(
                        "Could not remove file-operation transaction directory '%s': %s",
                        transaction_dir, exc,
                    )
        except (OSError, ValueError) as exc:
            log.warning("Blocked file operation: %s", exc)
            show_error(self, "File Operation Failed", str(exc))
            return False

    def setupShortcuts(self):
        QShortcut(QKeySequence("Ctrl+E"), self, self.openInExplorer)
        QShortcut(QKeySequence("Delete"), self, self.deleteSelected)
        QShortcut(QKeySequence("Ctrl+C"), self, self.copySelected)
        QShortcut(QKeySequence("Ctrl+X"), self, self.cutSelected)
        QShortcut(QKeySequence("Ctrl+V"), self, self.pasteIntoFolder)
        QShortcut(QKeySequence("F5"), self, self.refresh_view)
        QShortcut(QKeySequence("F2"), self, self.renameSelected)

    def contextMenuEvent(self, event):
        selected_index = self.treeView.currentIndex()
        if not selected_index.isValid():
            return

        menu = RoundMenu(parent=self)
        newMenu = RoundMenu("New", self)
        newMenu.setIcon(FIF.ADD)

        openAction = Action(FIF.VIEW, 'Open')
        openExplorerAction = Action(FIF.FOLDER, 'Open in Explorer')
        refreshAction = Action(FIF.SYNC, 'Refresh')
        copyAction = Action(FIF.COPY, 'Copy')
        pasteAction = Action(FIF.PASTE, 'Paste')
        cutAction = Action(FIF.CUT, 'Cut')
        deleteAction = Action(FIF.DELETE, 'Delete')
        newFileAction = Action(FIF.DOCUMENT, 'File')
        newFolderAction = Action(FIF.FOLDER, 'Folder')
        renameAction = Action(FIF.EDIT, 'Rename')

        openAction.triggered.connect(self.openSelected)
        openExplorerAction.triggered.connect(self.openInExplorer)
        refreshAction.triggered.connect(self.refresh_view)
        copyAction.triggered.connect(self.copySelected)
        pasteAction.triggered.connect(self.pasteIntoFolder)
        cutAction.triggered.connect(self.cutSelected)
        deleteAction.triggered.connect(self.deleteSelected)
        newFileAction.triggered.connect(self.createNewFile)
        newFolderAction.triggered.connect(self.createNewFolder)
        renameAction.triggered.connect(self.renameSelected)

        newMenu.addActions([newFileAction, newFolderAction])

        menu.addAction(openAction)
        menu.addAction(openExplorerAction)
        menu.addSeparator()
        menu.addAction(refreshAction)
        menu.addSeparator()
        menu.addAction(copyAction)
        menu.addAction(cutAction)
        menu.addAction(pasteAction)
        menu.addSeparator()
        menu.addAction(deleteAction)
        menu.addAction(renameAction)
        menu.addSeparator()
        menu.addMenu(newMenu)

        menu.exec(event.globalPos())

    def openSelected(self):
        self.on_double_click(self.treeView.currentIndex())

    def openInExplorer(self):
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            try:
                path = self._checked_path(
                    self.model.filePath(selected_index), allow_root=True
                )
                if os.path.exists(path):
                    if os.path.isfile(path):
                        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
                    else:
                        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                else:
                    raise ValueError("The selected path does not exist")
            except (OSError, ValueError) as exc:
                log.warning("Blocked opening an unsafe explorer path: %s", exc)
                show_warning(self, "Open Blocked", str(exc))

    def refresh_view(self):
        if hasattr(self, 'current_path') and self.current_path:
            if not self._root_is_safe(self.current_path):
                self._root_valid = False
                self.treeView.hide()
                return
            self.model.setRootPath(self.current_path)
            specificIndex = self.model.index(self.current_path)
            self.treeView.setRootIndex(specificIndex)
            if specificIndex.isValid():
                self.treeView.expand(specificIndex)
        
        self._updateBuildStatus()
    
    def _updateBuildStatus(self):
        if not self.main_gui:
            return
        checker = getattr(self.main_gui, "canMutateWorkspace", None)
        if checker and not checker():
            return
        
        if not hasattr(self.main_gui, 'current_build') or not self.main_gui.current_build:
            return
        
        if not hasattr(self.main_gui, 'session') or not self.main_gui.session:
            return
        
        try:
            content_dir = get_build_content_dir(self.main_gui.current_build.folder)
            effective_data = get_build_data(self.main_gui.session, self.main_gui.current_build)
            
            previous_status = self.main_gui.current_build.content_status
            self.main_gui.current_build.content_status = validate_build(
                self.main_gui.current_build,
                content_dir,
                self.main_gui.daz_folders,
                effective_values=effective_data
            )
            
            if hasattr(self.main_gui, 'buildListWidget') and self.main_gui.buildListWidget:
                self.main_gui.buildListWidget.refreshList()
            
            if previous_status != self.main_gui.current_build.content_status:
                self.main_gui.saveSession()
        except Exception as e:
            log.error(f"Error updating build status: {e}")

    def copySelected(self):
        if not self._mutation_allowed():
            return
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            try:
                source_path = self._checked_path(
                    self.model.filePath(selected_index), allow_root=False
                )
                self.clipboard = source_path
                self.isCutOperation = False
            except ValueError as exc:
                show_warning(self, "Copy Blocked", str(exc))

    def cutSelected(self):
        if not self._mutation_allowed():
            return
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            try:
                source_path = self._checked_path(
                    self.model.filePath(selected_index), allow_root=False
                )
                self.clipboard = source_path
                self.isCutOperation = True
            except ValueError as exc:
                show_warning(self, "Cut Blocked", str(exc))

    def pasteIntoFolder(self):
        if not (self.clipboard and os.path.exists(self.clipboard)):
            return
        destination_index = self.treeView.currentIndex()
        if destination_index.isValid():
            selected_path = self.model.filePath(destination_index)
            destination_path = (
                selected_path if os.path.isdir(selected_path)
                else os.path.dirname(selected_path)
            )
        else:
            destination_path = self.current_path

        succeeded = self._transfer_path(
            self.clipboard, destination_path, move=self.isCutOperation
        )
        if succeeded:
            self.clipboard = None
            self.isCutOperation = False
            QTimer.singleShot(0, self.refresh_view)

    def deleteSelected(self):
        if not self._mutation_allowed():
            return
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            try:
                target = self._checked_path(
                    self.model.filePath(selected_index), allow_root=False
                )
                reply = QMessageBox.question(
                    self, "Move to Recycle Bin?",
                    f"Move '{os.path.basename(target)}' to the Recycle Bin?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                if not move_to_trash(target):
                    permanent = QMessageBox.warning(
                        self, "Recycle Bin Unavailable",
                        "The item could not be moved to the Recycle Bin. Delete it "
                        "permanently? This cannot be undone.",
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    if permanent != QMessageBox.StandardButton.Yes:
                        return
                    self._remove_permanently(target)
                QTimer.singleShot(0, self.refresh_view)
                show_info(self, "Deletion Successful", "Item successfully deleted.")
            except (OSError, ValueError) as exc:
                log.warning("Deletion blocked or failed: %s", exc)
                show_error(self, "Deletion Failed", str(exc))

    def renameSelected(self):
        if not self._mutation_allowed():
            return
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            try:
                current_path = self._checked_path(
                    self.model.filePath(selected_index), allow_root=False
                )
                base_path = os.path.dirname(current_path)
                current_name = os.path.basename(current_path)
            except ValueError as exc:
                show_warning(self, "Rename Blocked", str(exc))
                return

            dialog = NameEntryDialog(
                self, title="Rename", placeholder="Enter new name"
            )
            dialog.nameLineEdit.setText(current_name)
            if dialog.exec():
                try:
                    new_name = validate_windows_name(dialog.getName())
                    new_path = safe_child_path(
                        self.current_path, base_path, new_name
                    )
                except ValueError as exc:
                    show_warning(self, "Renaming Failed", str(exc))
                    return
                if os.path.exists(new_path):
                    show_warning(
                        self, "Renaming Failed",
                        f"An item named <strong>{new_name}</strong> already exists.",
                        Qt.Vertical
                    )
                    return
                try:
                    os.rename(current_path, new_path)
                    QTimer.singleShot(0, self.refresh_view)
                except OSError as e:
                    log.error(f"Error renaming file {current_path} to {new_path}: {e}")
                    show_error(self, "Renaming Failed", str(e))

    def createNewFile(self):
        if not self._mutation_allowed():
            return
        dialog = NameEntryDialog(self, title="New File", placeholder="Enter file name")
        if dialog.exec():
            destination_index = self.treeView.currentIndex()
            destination_path = (
                self.model.filePath(destination_index)
                if destination_index.isValid()
                else self.model.rootPath()
            )
            if not os.path.isdir(destination_path):
                destination_path = os.path.dirname(destination_path)
            try:
                file_name = validate_windows_name(dialog.getName())
                destination_path = self._checked_path(
                    destination_path, allow_root=True
                )
                new_file_path = safe_child_path(
                    self.current_path, destination_path, file_name
                )
            except ValueError as exc:
                show_warning(self, "File Creation Failed", str(exc))
                return

            if os.path.exists(new_file_path):
                show_warning(self, "File Exists", f"{file_name} already exists.")
                return

            try:
                with open(new_file_path, 'x', encoding='utf-8'):
                    pass
                QTimer.singleShot(0, self.refresh_view)
                show_info(self, "File Created", f"New file created: {file_name}")
                log.info(f"New file created: {new_file_path}")
            except IOError as e:
                show_error(self, "Error", f"Error creating file {file_name}: {e}")
                log.error(f"Error creating file {new_file_path}: {e}")

    def createNewFolder(self):
        if not self._mutation_allowed():
            return
        dialog = NameEntryDialog(self, title="New Folder", placeholder="Enter folder name")
        if dialog.exec():
            destination_index = self.treeView.currentIndex()
            destination_path = (
                self.model.filePath(destination_index)
                if destination_index.isValid()
                else self.model.rootPath()
            )
            if not os.path.isdir(destination_path):
                destination_path = os.path.dirname(destination_path)
            try:
                folder_name = validate_windows_name(dialog.getName())
                destination_path = self._checked_path(
                    destination_path, allow_root=True
                )
                new_folder_path = safe_child_path(
                    self.current_path, destination_path, folder_name
                )
            except ValueError as exc:
                show_warning(self, "Folder Creation Failed", str(exc))
                return

            if os.path.exists(new_folder_path):
                show_warning(self, "Folder Exists", f"The folder {folder_name} already exists.")
                return

            try:
                os.mkdir(new_folder_path)
                QTimer.singleShot(0, self.refresh_view)
                show_info(self, "Folder Created", f"New folder created: {folder_name}")
                log.info(f"New folder created: {new_folder_path}")
            except OSError as e:
                show_error(self, "Error", f"Error creating folder {folder_name}.")
    
    def setRootPath(self, path: str):
        if not self._root_is_safe(path):
            self.current_path = os.path.abspath(path) if path else ""
            self._root_valid = False
            self.clipboard = None
            self.isCutOperation = False
            self.treeView.hide()
            return False
        self._root_valid = True
        self.current_path = os.path.abspath(path)
        self.clipboard = None
        self.isCutOperation = False
        self.model.setRootPath(self.current_path)
        specific_index = self.model.index(self.current_path)
        self.treeView.setRootIndex(specific_index)
        self.treeView.show()
        if specific_index.isValid() and self.model.canFetchMore(specific_index):
            self.model.fetchMore(specific_index)
        return True
    
    def _expandFolders(self, parent_path: str, content_path: str):
        parent_index = self.model.index(parent_path)
        if parent_index.isValid():
            self.treeView.expand(parent_index)
            
            content_index = self.model.index(content_path)
            if content_index.isValid():
                if self.model.canFetchMore(content_index):
                    self.model.fetchMore(content_index)
                self.treeView.expand(content_index)
    
    def reset_model(self):
        current_path = self.current_path
        
        old_model = self.model
        self.model = QFileSystemModel()
        self.model.setRootPath(current_path)
        self.treeView.setModel(self.model)
        
        if old_model:
            old_model.deleteLater()
        
        if not os.path.exists(current_path):
            return
            
        specificIndex = self.model.index(current_path)
        self.treeView.setRootIndex(specificIndex)
        
        if specificIndex.isValid() and self.model.canFetchMore(specificIndex):
            self.model.fetchMore(specificIndex)


class BuildListWidget(QWidget):
    buildSelected = Signal(str)
    buildAdded = Signal()
    buildDeleted = Signal(str)
    buildCheckedChanged = Signal(str, bool)
    buildsReordered = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.selected_build_id = None
        self._refreshing = False
        
        self.initUI()

    def _can_mutate(self) -> bool:
        main_gui = self.parent()
        checker = getattr(main_gui, "canMutateWorkspace", None)
        return self.isEnabled() and (not checker or checker())
    
    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        self.listWidget = QListWidget(self)
        
        self.listWidget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.listWidget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.listWidget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        
        self.listWidget.setDropIndicatorShown(True)
        
        self.listWidget.model().rowsMoved.connect(self.onRowsMoved)
        
        self.listWidget.setStyleSheet("""
            QListWidget {
                background-color: rgb(45, 45, 45);
                border: 1px solid rgb(60, 60, 60);
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget::item {
                color: white;
                padding: 8px;
                border-radius: 4px;
                margin: 2px;
                border: none;
                outline: none;
            }
            QListWidget::item:hover {
                background-color: rgb(55, 55, 55);
            }
            QListWidget::item:selected {
                background-color: rgb(0, 120, 215);
                color: white;
                border: none;
                outline: none;
            }
            QListWidget::indicator {
                width: 14px;
                height: 14px;
                border: 2px solid rgb(120, 120, 120);
                border-radius: 3px;
                background-color: rgb(35, 35, 35);
            }
            QListWidget::indicator:hover {
                border: 2px solid rgb(180, 180, 180);
                background-color: rgb(50, 50, 50);
            }
            QListWidget::indicator:checked {
                background-color: rgb(0, 120, 215);
                border: 2px solid rgb(0, 150, 255);
                image: url(data:image/svg+xml;base64,""" + CHECKBOX_CHECKMARK_BASE64 + """);
            }
            QListWidget::indicator:unchecked {
                background-color: rgb(35, 35, 35);
                border: 2px solid rgb(120, 120, 120);
            }
            QListWidget::indicator:checked:selected {
                background-color: rgb(0, 150, 255);
                border: 2px solid rgb(100, 200, 255);
            }
            QListWidget::indicator:unchecked:selected {
                background-color: rgb(45, 45, 45);
                border: 2px solid rgb(180, 180, 180);
            }
        """)
        self.listWidget.itemClicked.connect(self.onItemClicked)
        layout.addWidget(self.listWidget)
        
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        
        self.addButton = PrimaryPushButton("+ Add Build", self)
        self.addButton.clicked.connect(self.onAddBuild)
        button_row.addWidget(self.addButton)
        
        self.newSessionButton = PushButton("New Session", self)
        self.newSessionButton.setIcon(FIF.UPDATE)
        self.newSessionButton.setToolTip("Start a new session (deletes all builds and content)")
        self.newSessionButton.clicked.connect(self.onNewSession)
        button_row.addWidget(self.newSessionButton)
        
        layout.addLayout(button_row)
        
        self.listWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listWidget.customContextMenuRequested.connect(self.showContextMenu)
    
    def setSession(self, session):
        self.session = session
        self.refreshList()
    
    def refreshList(self):
        if getattr(self, "_refreshing", False):
            return

        if not self.session:
            return

        self._refreshing = True
        try:
            self.listWidget.clear()
            
            show_delete_buttons = len(self.session.builds) > 1
            
            for build in self.session.builds:
                status_icon = self.getStatusIcon(build.content_status)
                
                item_text = f"{status_icon} Build {build.part:02d}"
                
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, build.id)
                
                self.listWidget.addItem(item)
                
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(8, 4, 8, 4)
                row_layout.setSpacing(8)
                
                checkbox = QCheckBox()
                checkbox.setChecked(build.checked)
                checkbox.setProperty("build_id", build.id)
                checkbox.stateChanged.connect(self._onCheckboxStateChanged)
                checkbox.setStyleSheet("""
                    QCheckBox::indicator {
                        width: 14px;
                        height: 14px;
                        border: 2px solid rgb(120, 120, 120);
                        border-radius: 3px;
                        background-color: rgb(35, 35, 35);
                    }
                    QCheckBox::indicator:hover {
                        border: 2px solid rgb(180, 180, 180);
                        background-color: rgb(50, 50, 50);
                    }
                    QCheckBox::indicator:checked {
                        background-color: rgb(0, 120, 215);
                        border: 2px solid rgb(0, 150, 255);
                        image: url(data:image/svg+xml;base64,""" + CHECKBOX_CHECKMARK_BASE64 + """);
                    }
                    QCheckBox::indicator:unchecked {
                        background-color: rgb(35, 35, 35);
                        border: 2px solid rgb(120, 120, 120);
                    }
                """)
                row_layout.addWidget(checkbox)
                
                text_label = QLabel(item_text)
                text_label.setStyleSheet("color: white;")
                row_layout.addWidget(text_label, 1)
                
                if show_delete_buttons:
                    delete_btn = ToolButton(FIF.DELETE, row_widget)
                    delete_btn.setFixedSize(24, 24)
                    delete_btn.setToolTip("Delete this build")
                    delete_btn.setProperty("build_id", build.id)
                    delete_btn.clicked.connect(self._onDeleteButtonClicked)
                    row_layout.addWidget(delete_btn)
                
                self.listWidget.setItemWidget(item, row_widget)
            
            if self.selected_build_id:
                self.selectBuild(self.selected_build_id)
            elif self.session.builds:
                self.selectBuild(self.session.builds[0].id)
        finally:
            self._refreshing = False
    
    def getStatusIcon(self, status: str) -> str:
        status_icons = {
            "ready": "✅",
            "incomplete": "⚠️",
            "empty": "📭"
        }
        return status_icons.get(status, "📭")
    
    def selectBuild(self, build_id: str):
        for i in range(self.listWidget.count()):
            item = self.listWidget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == build_id:
                current_item = self.listWidget.currentItem()
                current_build_id = (
                    current_item.data(Qt.ItemDataRole.UserRole)
                    if current_item is not None
                    else None
                )
                if current_build_id != build_id:
                    self.listWidget.setCurrentItem(item)
                if build_id != self.selected_build_id:
                    self.selected_build_id = build_id
                    self.buildSelected.emit(build_id)
                break
    
    def onItemClicked(self, item):
        if not self._can_mutate():
            self.refreshList()
            return
        build_id = item.data(Qt.ItemDataRole.UserRole)
        self.selected_build_id = build_id
        self.buildSelected.emit(build_id)
    
    def _onCheckboxStateChanged(self, state):
        if self._refreshing:
            return
        if not self._can_mutate():
            self.refreshList()
            return

        if not self.session:
            return
        
        checkbox = self.sender()
        if not checkbox:
            return
        
        build_id = checkbox.property("build_id")
        is_checked = checkbox.isChecked()
        
        for build in self.session.builds:
            if build.id == build_id:
                build.checked = is_checked
                break
        
        self.buildCheckedChanged.emit(build_id, is_checked)
    
    def onRowsMoved(self, parent, start, end, destination, row):
        if not self.session or self._refreshing:
            return
        if not self._can_mutate():
            self.refreshList()
            return
        
        log.info(f"Drag-drop reorder detected: moved row {start} to position {row}")
        
        try:
            new_order = []
            for i in range(self.listWidget.count()):
                item = self.listWidget.item(i)
                if item:
                    build_id = item.data(Qt.ItemDataRole.UserRole)
                    if build_id:
                        new_order.append(build_id)
            
            if len(new_order) != len(self.session.builds):
                log.error(f"Reorder failed: UI has {len(new_order)} items but session has {len(self.session.builds)} builds")
                self.refreshList()
                return
            
            reorder_builds(self.session, new_order)
            
            self.refreshList()
            
            self.buildsReordered.emit()
            
            log.info("Build reordering completed successfully")
            
        except (ValueError, KeyError, AttributeError) as e:
            log.error(f"Error during build reordering: {e}")
            self.refreshList()
            show_error(self.parent(), "Reorder Error", f"Failed to reorder builds: {e}")
    
    
    def getCheckedBuilds(self):
        if not self.session:
            return []
        return [build for build in self.session.builds if build.checked]
    
    def setChecked(self, build_id: str, checked: bool):
        if not self.session:
            return
        
        for build in self.session.builds:
            if build.id == build_id:
                build.checked = checked
                break
        
        for i in range(self.listWidget.count()):
            item = self.listWidget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == build_id:
                row_widget = self.listWidget.itemWidget(item)
                if row_widget:
                    checkbox = row_widget.findChild(QCheckBox)
                    if checkbox:
                        checkbox.blockSignals(True)
                        checkbox.setChecked(checked)
                        checkbox.blockSignals(False)
                break
    
    def clearAllChecks(self):
        if not self.session:
            return
        
        for build in self.session.builds:
            build.checked = False
        
        for i in range(self.listWidget.count()):
            item = self.listWidget.item(i)
            row_widget = self.listWidget.itemWidget(item)
            if row_widget:
                checkbox = row_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.blockSignals(True)
                    checkbox.setChecked(False)
                    checkbox.blockSignals(False)
    
    def onAddBuild(self):
        if not self.session or not self._can_mutate():
            return
        
        self.buildAdded.emit()
    
    def onNewSession(self):
        if not self._can_mutate():
            return
        main_gui = self.parent()
        if main_gui and hasattr(main_gui, 'onNewSession'):
            main_gui.onNewSession()
    
    def showContextMenu(self, position):
        item = self.listWidget.itemAt(position)
        if not item:
            return
        
        build_id = item.data(Qt.ItemDataRole.UserRole)
        
        menu = RoundMenu(parent=self)
        
        cleanAction = Action(FIF.DELETE, "Clean Content")
        cleanAction.triggered.connect(lambda: self.onCleanContent(build_id))
        menu.addAction(cleanAction)
        
        menu.exec(QCursor.pos())
    
    def _onDeleteButtonClicked(self):
        sender = self.sender()
        if sender:
            build_id = sender.property("build_id")
            if build_id:
                self.onDeleteBuild(build_id)
    
    def onCleanContent(self, build_id: str):
        if not self.session or not self._can_mutate():
            return
        
        build = None
        for b in self.session.builds:
            if b.id == build_id:
                build = b
                break
        
        if not build:
            return
        
        msg = QMessageBox(self)
        msg.setWindowTitle("Clean Content")
        msg.setText(f"Clean content for Build {build.part:02d}?")
        msg.setInformativeText("This will delete all files including Manifest.dsx and Supplement.dsx.\nThis action cannot be undone.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        
        if msg.exec() == QMessageBox.StandardButton.Yes:
            try:
                clean_build_content(build.folder)
                build.content_status = "empty"
                
                main_gui = self.parent()
                if main_gui and hasattr(main_gui, 'saveSession'):
                    main_gui.saveSession()
                
                if main_gui and hasattr(main_gui, 'fileExplorer'):
                    main_gui.fileExplorer.reset_model()
                
                self.refreshList()
                show_info(self.parent(), "Content Cleaned", f"Content cleaned for Build {build.part:02d}")
            except Exception as e:
                show_error(self.parent(), "Error", f"Failed to clean content: {e}")
    
    def onDeleteBuild(self, build_id: str):
        if not self.session or not self._can_mutate():
            return
        
        build = None
        for b in self.session.builds:
            if b.id == build_id:
                build = b
                break
        
        if not build:
            return
        
        msg = QMessageBox(self)
        msg.setWindowTitle("Delete Build")
        msg.setText(f"Delete Build {build.part:02d}?")
        msg.setInformativeText("This will delete the build folder and all its contents.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self.buildDeleted.emit(build_id)
