import os
import shutil
import tempfile
import base64

from PySide6.QtWidgets import (
    QMessageBox, QWidget, QLabel, QDialog, QVBoxLayout, QFileDialog,
    QHBoxLayout, QFileSystemModel
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QEasingCurve, QUrl, QTimer
)
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply
)
from PySide6.QtGui import (
    QPixmap, QImage, QCursor, QDesktopServices, QKeySequence,
    QShortcut
)
from qfluentwidgets import (
    setTheme, Theme, PrimaryPushButton, PushButton, Action, RoundMenu, LineEdit,
    InfoBar, InfoBarPosition, InfoBarIcon,
    CompactSpinBox, TogglePushButton, FlowLayout, TreeView,
    MessageBoxBase, SubtitleLabel
)
from qfluentwidgets import FluentIcon as FIF

from utils import resource_path, calculate_total_files, show_warning, show_error, show_info
from logger_utils import get_logger

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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.imagePath = ""
        self._ownedTemp = False
        self.defaultText = "Drop Image Here\nOr Click to Select"
        self.placeholder_image_rel = os.path.join('assets', 'images', 'placeholder', 'imageexport.png')
        self.placeholder_max_px = 96
        self._load_seq = 0

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

    def resetToPlaceholder(self):
        self.loadPlaceholderImage()

    def setImagePath(self, path):
        if not path or not os.path.exists(path):
            self.resetToPlaceholder()
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.resetToPlaceholder()
            return
        self.imagePath = path
        self._ownedTemp = False
        self._orig_pixmap = pm
        self._is_placeholder = False
        self._apply_scaled_pixmap()
        self.removeImageButton.show()
        self.updateButtonPosition()

    def removeImage(self):
        try:
            if self._ownedTemp and self.imagePath and os.path.exists(self.imagePath):
                os.remove(self.imagePath)
        except Exception:
            pass
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
                    local_path = u.toLocalFile()
                    try:
                        sys_tmp = os.path.abspath(tempfile.gettempdir())
                        if os.path.commonpath([os.path.abspath(local_path), sys_tmp]) == sys_tmp:
                            self._adopt_local_as_temp(local_path)
                        else:
                            self.setImagePath(local_path)
                        handled = True
                        break
                    except Exception:
                        self._adopt_local_as_temp(local_path)
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
            self.setImagePath(filePath)

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
            return

        url = urls[0]
        req = QNetworkRequest(url)
        reply = self._nam.get(req)

        def _finished():
            if seq != self._load_seq:
                reply.deleteLater()
                return

            try:
                if reply.error() != QNetworkReply.NoError:
                    reply.deleteLater()
                    self._download_first_valid(urls[1:], seq)
                    return

                data = reply.readAll()
                pm = QPixmap()
                if not pm.loadFromData(bytes(data)):
                    reply.deleteLater()
                    self._download_first_valid(urls[1:], seq)
                    return

                fd, temp_path = tempfile.mkstemp(prefix="dimcreator_img_", suffix=".jpg")
                os.close(fd)
                pm.toImage().save(temp_path)
                if seq == self._load_seq:
                    self._set_owned_temp_path(temp_path)
                else:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

            finally:
                reply.deleteLater()

        reply.finished.connect(_finished)

    def _adopt_qimage_as_temp(self, qimg: QImage, suffix=".png"):
        try:
            fd, temp_path = tempfile.mkstemp(prefix="dimcreator_img_", suffix=suffix)
            os.close(fd)
            qimg.save(temp_path)
            self._set_owned_temp_path(temp_path)
        except Exception:
            self.resetToPlaceholder()

    def _adopt_local_as_temp(self, src_path: str):
        try:
            ext = os.path.splitext(src_path)[1] or ".png"
            fd, temp_path = tempfile.mkstemp(prefix="dimcreator_img_", suffix=ext)
            os.close(fd)
            shutil.copy2(src_path, temp_path)
            self._set_owned_temp_path(temp_path)
        except Exception:
            self.resetToPlaceholder()

    def _set_owned_temp_path(self, temp_path: str):
        try:
            if self._ownedTemp and self.imagePath and os.path.exists(self.imagePath):
                os.remove(self.imagePath)
        except Exception:
            pass

        pm = QPixmap(temp_path)
        if pm.isNull():
            try:
                os.remove(temp_path)
            except Exception:
                pass
            self.resetToPlaceholder()
            return

        self.imagePath = temp_path
        self._ownedTemp = True
        self._orig_pixmap = pm
        self._is_placeholder = False
        self._apply_scaled_pixmap()
        self.removeImageButton.show()
        self.updateButtonPosition()

    def _download_url_to_temp(self, url: QUrl):
        req = QNetworkRequest(url)
        reply = self._nam.get(req)

        def _finished():
            try:
                if reply.error() != QNetworkReply.NoError:
                    self.resetToPlaceholder()
                    reply.deleteLater()
                    return
                data = reply.readAll()
                pm = QPixmap()
                if not pm.loadFromData(bytes(data)):
                    self.resetToPlaceholder()
                    reply.deleteLater()
                    return
                fd, temp_path = tempfile.mkstemp(prefix="dimcreator_img_", suffix=".jpg")
                os.close(fd)
                img = pm.toImage()
                img.save(temp_path)
                self._set_owned_temp_path(temp_path)
            finally:
                reply.deleteLater()

        reply.finished.connect(_finished)

    def _adopt_data_url(self, url: QUrl) -> bool:
        try:
            s = url.toString()
            if not s.startswith('data:image/'):
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
                raw = base64.b64decode(b, validate=False)
            else:
                raw = QUrl.fromPercentEncoding(data.encode('utf-8'))
                if not isinstance(raw, (bytes, bytearray)):
                    raw = bytes(raw)

            pm = QPixmap()
            if not pm.loadFromData(raw):
                return False

            fd, temp_path = tempfile.mkstemp(prefix="dimcreator_img_", suffix=ext or '.png')
            os.close(fd)
            pm.toImage().save(temp_path)
            self._set_owned_temp_path(temp_path)
            return True
        except Exception:
            return False


class ZipThread(QThread):
    succeeded = Signal()
    error = Signal(str)
    progressUpdated = Signal(int)

    def __init__(self, content_dir, prefix, sku, product_part, product_name, destination_folder, zip_function):
        super().__init__()
        self.content_dir = content_dir
        self.prefix = prefix
        self.sku = sku
        self.product_part = product_part
        self.product_name = product_name
        self.destination_folder = destination_folder
        self.zip_function = zip_function

    def run(self):
        try:
            total_files = max(1, calculate_total_files(self.content_dir))
            self.zip_function(
                self.content_dir,
                self.prefix,
                self.sku,
                self.product_part,
                self.product_name,
                self.destination_folder,
                self.reportProgress,
                total_files
            )
            self.succeeded.emit()
        except Exception as e:
            self.error.emit(str(e))

    def reportProgress(self, percent):
        self.progressUpdated.emit(percent)


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
        self.yesButton.setEnabled(bool(text.strip()))

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

        destinationIndex = self.indexAt(event.pos())
        destinationPath = (
            self.model().filePath(destinationIndex)
            if destinationIndex.isValid()
            else self.parent().dimbuild_dir
        )

        normDestinationPath = os.path.normpath(destinationPath).lower()
        normDimBuildDir = os.path.normpath(self.parent().dimbuild_dir).lower()

        if not normDestinationPath.startswith(normDimBuildDir):
            print(f"Attempt to drop outside DIMBuild directory: {normDestinationPath} to {normDimBuildDir}")
            log.warning(f"Attempt to drop outside DIMBuild directory: {normDestinationPath} to {normDimBuildDir}")
            self.parent().InvalidFolderInfoBar()
            event.ignore()
            return

        any_file_op = False

        for url in event.mimeData().urls():
            sourcePath = url.toLocalFile()
            try:
                if sourcePath.lower().endswith(('.zip', '.rar', '.7z')):
                    if self.parent().main_gui:
                        self.parent().main_gui.dropExtractArchive(sourcePath)
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
        if not os.path.isdir(destinationPath):
            destinationPath = os.path.dirname(destinationPath)

        if not os.path.isdir(destinationPath):
            print(f"Invalid destination path for copy: {destinationPath}")
            log.error(f"Invalid destination path for copy: {destinationPath}")
            return

        basename = os.path.basename(sourcePath.rstrip(os.sep))
        target = os.path.join(destinationPath, basename)

        try:
            src_abs = os.path.abspath(sourcePath)
            tgt_abs = os.path.abspath(target)
            if os.path.isdir(src_abs):
                common = os.path.commonpath([src_abs, tgt_abs])
                if common == src_abs:
                    print(f"Copy blocked: {src_abs} -> {tgt_abs} (self/subfolder).")
                    log.warning(f"Copy blocked: {src_abs} -> {tgt_abs} (self/subfolder).")
                    return
            try:
                if os.path.exists(tgt_abs) and os.path.samefile(src_abs, tgt_abs):
                    print("Copy skipped: source and target are the same.")
                    log.info("Copy skipped: same source and target.")
                    return
            except Exception:
                pass
        except Exception:
            pass

        if os.path.exists(target):
            if not self.overwrite_all:
                reply = QMessageBox.question(
                    self.parent(),
                    "Item exists",
                    f"'{basename}' already exists. Overwrite (replace)?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.YesToAll,
                    QMessageBox.StandardButton.No
                )
            else:
                reply = QMessageBox.StandardButton.Yes

            if reply == QMessageBox.StandardButton.No:
                print("Copy canceled by user.")
                log.info("Copy canceled by user (overwrite denied).")
                return
            if reply == QMessageBox.StandardButton.YesToAll:
                self.overwrite_all = True

            try:
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
            except Exception as e:
                log.error(f"Failed to remove existing target '{target}': {e}")
                return

        try:
            if os.path.isdir(sourcePath):
                shutil.copytree(sourcePath, target)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(sourcePath, target)
            print(f"Item copied: {sourcePath} -> {target}")
            log.info(f"Item copied: {sourcePath} -> {target}")
        except Exception as e:
            print(f"Error copying {sourcePath} to {target}: {e}")
            log.error(f"Error copying {sourcePath} to {target}: {e}")
            self.parent().InvalidFolderInfoBar()

    def movePath(self, sourcePath, destinationPath):
        if not os.path.isdir(destinationPath):
            destinationPath = os.path.dirname(destinationPath)

        if not os.path.isdir(destinationPath):
            print(f"Invalid destination path for move: {destinationPath}")
            log.error(f"Invalid destination path for move: {destinationPath}")
            return

        basename = os.path.basename(sourcePath.rstrip(os.sep))
        target = os.path.join(destinationPath, basename)

        try:
            src_abs = os.path.abspath(sourcePath)
            tgt_abs = os.path.abspath(target)
            if os.path.isdir(src_abs):
                common = os.path.commonpath([src_abs, tgt_abs])
                if common == src_abs:
                    print(f"Move blocked: {src_abs} -> {tgt_abs} (self/subfolder).")
                    log.warning(f"Move blocked: {src_abs} -> {tgt_abs} (self/subfolder).")
                    return
            try:
                if (os.path.exists(tgt_abs) and
                        os.path.samefile(src_abs, tgt_abs)):
                    print("Move skipped: source and target are the same.")
                    log.info("Move skipped: same source and target.")
                    return
            except Exception:
                pass
        except Exception:
            pass

        if os.path.exists(target):
            if not self.overwrite_all:
                reply = QMessageBox.question(
                    self.parent(),
                    "Item exists",
                    f"'{basename}' already exists. Overwrite (replace)?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.YesToAll,
                    QMessageBox.StandardButton.No
                )
            else:
                reply = QMessageBox.StandardButton.Yes

            if reply == QMessageBox.StandardButton.No:
                print("Move canceled by user.")
                log.info("Move canceled by user (overwrite denied).")
                return
            if reply == QMessageBox.StandardButton.YesToAll:
                self.overwrite_all = True

            try:
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
            except Exception as e:
                log.error(f"Failed to remove existing target '{target}': {e}")
                return

        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(sourcePath, target)
            print(f"Item moved: {sourcePath} -> {target}")
            log.info(f"Item moved: {sourcePath} -> {target}")
        except Exception as e:
            print(f"Error moving {sourcePath} to {target}: {e}")
            log.error(f"Error moving {sourcePath} to {target}: {e}")
            self.parent().InvalidFolderInfoBar()


class FileExplorer(QWidget):
    def __init__(self, path=os.path.expanduser("~"), parent=None,
                 dimbuild_dir="", main_gui=None):
        super().__init__(parent)
        self.dimbuild_dir = dimbuild_dir
        self.main_gui = main_gui

        self.clipboard = None
        self.isCutOperation = False

        self.model = QFileSystemModel()
        self.model.setRootPath('')
        self.treeView = CustomTreeView(self)
        self.treeView.setModel(self.model)
        self.treeView.setExpandsOnDoubleClick(False)

        self.treeView.setSortingEnabled(True)
        self.treeView.sortByColumn(0, Qt.AscendingOrder)

        self.treeView.setAcceptDrops(True)
        self.treeView.setDragEnabled(True)
        self.treeView.setDragDropMode(TreeView.DragDropMode.DragDrop)

        specificIndex = self.model.index(path)
        self.treeView.setRootIndex(specificIndex)

        self.treeView.setColumnWidth(0, 360)
        self.treeView.setColumnWidth(1, 100)
        self.treeView.setColumnWidth(2, 120)
        self.treeView.setColumnWidth(3, 150)
        self.treeView.doubleClicked.connect(self.on_double_click)

        self.setupShortcuts()

    def on_double_click(self, index):
        try:
            path = self.model.filePath(index)
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
        self.treeView.setGeometry(0, 0, self.width(), self.height())
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
            path = self.model.filePath(selected_index)
            if os.path.exists(path):
                try:
                    if os.path.isfile(path):
                        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
                    else:
                        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                except Exception as e:
                    print(f"Error opening the path in explorer: {e}")
                    log.error(f"Error opening the path in explorer: {e}")
            else:
                print("Error: The selected path does not exist.")
                log.warning("Error: The selected path does not exist.")

    def refresh_view(self):
        root = self.treeView.model().rootPath()
        self.model.setRootPath('')
        self.model.setRootPath(root)

    def reinitialize_model(self, newRootPath):
        self.model = QFileSystemModel()
        self.model.setRootPath('')

        self.treeView.setModel(self.model)
        self.treeView.setRootIndex(self.model.index(newRootPath))

        self.treeView.setExpandsOnDoubleClick(False)

        specificIndex = self.model.index(newRootPath)
        self.treeView.setRootIndex(specificIndex)
        self.treeView.expand(specificIndex)

    def copySelected(self):
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            source_path = self.model.filePath(selected_index)
            if os.path.exists(source_path):
                self.clipboard = source_path
                self.isCutOperation = False
                print(f"Item copied: {self.clipboard}")
                log.info(f"Item copied: {self.clipboard}")
            else:
                print("Error: The selected item does not exist.")
                log.error(f"Error: The selected item does not exist. - {source_path}")

    def cutSelected(self):
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            source_path = self.model.filePath(selected_index)
            if os.path.exists(source_path):
                self.clipboard = source_path
                self.isCutOperation = True
                print(f"Item cut: {self.clipboard}")
                log.info(f"Item cut: {self.clipboard}")
            else:
                print("Error: The selected item does not exist.")
                log.error(f"Error: The selected item does not exist. - {source_path}")

    def pasteIntoFolder(self):
        if not (self.clipboard and os.path.exists(self.clipboard)):
            print("Nothing to paste or source no longer exists.")
            log.warning("Paste aborted: empty clipboard or missing source.")
            return

        destination_index = self.treeView.currentIndex()
        if destination_index.isValid():
            selected_path = self.model.filePath(destination_index)
            destination_path = (
                selected_path if os.path.isdir(selected_path)
                else os.path.dirname(selected_path)
            )
        else:
            destination_path = self.model.rootPath()

        if not os.path.isdir(destination_path):
            print("Invalid destination path for paste operation.")
            log.error(f"Invalid destination path for paste operation: {destination_path}")
            return

        basename = os.path.basename(self.clipboard.rstrip(os.sep))
        target = os.path.join(destination_path, basename)

        try:
            src_abs = os.path.abspath(self.clipboard)
            tgt_abs = os.path.abspath(target)
            if os.path.isdir(src_abs):
                common = os.path.commonpath([src_abs, tgt_abs])
                if common == src_abs:
                    show_warning(
                        self, "Invalid Operation",
                        "Cannot paste a folder into itself or its subfolder.",
                        Qt.Vertical
                    )
                    log.warning(f"Paste blocked: {src_abs} -> {tgt_abs} (self/subfolder).")
                    return
            if os.path.samefile(self.clipboard, target):
                show_info(
                    self, "Operation Skipped",
                    "Source and destination are the same."
                )
                log.info(
                    f"Paste skipped: same source and destination: {src_abs}"
                )
                return
        except Exception:
            pass

        if os.path.exists(target):
            reply = QMessageBox.question(
                self,
                "File exists",
                f"The item '{basename}' already exists in the destination. Overwrite?",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                print("Operation canceled by the user.")
                log.info("Paste canceled by user (overwrite denied).")
                show_info(
                    self, "Operation Canceled",
                    f"Item <strong>{basename}</strong> not moved/copied."
                )
                return

            try:
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
            except Exception as e:
                log.error(f"Failed to remove existing target '{target}': {e}")
                show_error(
                    self, "Overwrite Failed",
                    f"Could not remove existing target.<br><small>{e}</small>"
                )
                return

        try:
            if self.isCutOperation:
                shutil.move(self.clipboard, target)
                print(f"Item moved: {self.clipboard} -> {destination_path}")
                log.info(f"Item moved: {self.clipboard} -> {destination_path}")
                show_info(
                    self, "Moving Successful",
                    f"Item <strong>{basename}</strong> successfully moved."
                )
            else:
                if os.path.isdir(self.clipboard):
                    shutil.copytree(self.clipboard, target)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(self.clipboard, target)
                print(f"Item copied: {self.clipboard} -> {destination_path}")
                log.info(f"Item copied: {self.clipboard} -> {destination_path}")
                show_info(
                    self, "Copying Successful",
                    f"Item <strong>{basename}</strong> successfully copied."
                )
        except Exception as e:
            print(f"Error during paste operation: {e}")
            log.error(f"Error during paste operation: {e}")
            show_error(
                self, "Paste Failed",
                f"Error during paste operation.<br><small>{e}</small>"
            )
        finally:
            self.clipboard = None
            self.isCutOperation = False
            QTimer.singleShot(0, self.refresh_view)

    def deleteSelected(self):
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            target = self.model.filePath(selected_index)
            try:
                if os.path.isdir(target):
                    shutil.rmtree(target)
                    QTimer.singleShot(0, self.refresh_view)
                elif os.path.isfile(target):
                    os.remove(target)
                    QTimer.singleShot(0, self.refresh_view)
                print(f"Item deleted: {target}")
                log.info(f"Item deleted: {target}")
                show_info(self, "Deletion Successful", "Item successfully deleted.")
            except OSError as e:
                print(f"Failed to delete the selected item. Error encountered: {e}")
                log.error(f"Failed to delete the selected item. Error encountered: {e}")
                show_error(
                    self, 'Deletion Failed',
                    "Failed to delete the selected item. Please try again or "
                    "check for file permissions."
                )

    def renameSelected(self):
        selected_index = self.treeView.currentIndex()
        if selected_index.isValid():
            current_path = self.model.filePath(selected_index)
            base_path = os.path.dirname(current_path)
            current_name = os.path.basename(current_path)

            dialog = NameEntryDialog(
                self, title="Rename", placeholder="Enter new name"
            )
            dialog.nameLineEdit.setText(current_name)
            if dialog.exec():
                new_name = dialog.getName()
                new_path = os.path.join(base_path, new_name)
                if os.path.exists(new_path):
                    print("Error: A file or folder with the new name already exists.")
                    log.error(
                        f"Error: Failed to rename item <strong>{current_name}"
                        f"</strong> into <strong>{new_name}</strong>. A file or "
                        f"folder with the <strong>{new_name}</strong> already "
                        "exists."
                    )
                    show_warning(
                        self, "Renaming Failed",
                        f"Failed to rename item <strong>{current_name}</strong> "
                        f"into <strong>{new_name}</strong>. A file or folder with "
                        f"the <strong>{new_name}</strong> already exists.",
                        Qt.Vertical
                    )
                    return
                try:
                    os.rename(current_path, new_path)
                    QTimer.singleShot(0, self.refresh_view)
                except OSError as e:
                    print(f"Error renaming file {current_path} to {new_path}: {e}")
                    log.error(f"Error renaming file {current_path} to {new_path}: {e}")

    def createNewFile(self):
        dialog = NameEntryDialog(self, title="New File", placeholder="Enter file name")
        if dialog.exec():
            file_name = dialog.getName()
            if not file_name.strip():
                show_warning(self, "Warning", "File name cannot be empty.")
                return

            destination_index = self.treeView.currentIndex()
            destination_path = (
                self.model.filePath(destination_index)
                if destination_index.isValid()
                else self.model.rootPath()
            )
            if not os.path.isdir(destination_path):
                destination_path = os.path.dirname(destination_path)
            new_file_path = os.path.join(
                destination_path, file_name if file_name else "New File.txt"
            )

            if os.path.exists(new_file_path):
                overwrite_reply = QMessageBox.question(
                    self,
                    "File Exists",
                    f"{file_name} already exists. Do you want to overwrite it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if overwrite_reply == QMessageBox.StandardButton.No:
                    return

            try:
                with open(new_file_path, 'w') as file:
                    file.close()
                QTimer.singleShot(0, self.refresh_view)
                show_info(self, "File Created", f"New file created: {file_name}")
                print(f"New file created: {new_file_path}")
                log.info(f"New file created: {new_file_path}")
            except IOError as e:
                show_error(self, "Error", f"Error creating file {file_name}: {e}")
                print(f"Error creating file {new_file_path}: {e}")
                log.error(f"Error creating file {new_file_path}: {e}")

    def createNewFolder(self):
        dialog = NameEntryDialog(self, title="New Folder", placeholder="Enter folder name")
        if dialog.exec():
            folder_name = dialog.getName()
            if not folder_name.strip():
                show_warning(self, "Warning", "Folder name cannot be empty.")
                return

            destination_index = self.treeView.currentIndex()
            destination_path = (
                self.model.filePath(destination_index)
                if destination_index.isValid()
                else self.model.rootPath()
            )
            if not os.path.isdir(destination_path):
                destination_path = os.path.dirname(destination_path)
            new_folder_path = os.path.join(
                destination_path, folder_name if folder_name else "New Folder"
            )

            if os.path.exists(new_folder_path):
                show_warning(self, "Folder Exists", f"The folder {folder_name} already exists.")
                return

            try:
                os.makedirs(new_folder_path, exist_ok=True)
                QTimer.singleShot(0, self.refresh_view)
                show_info(self, "Folder Created", f"New folder created: {folder_name}")
                print(f"New folder created: {new_folder_path}")
                log.info(f"New folder created: {new_folder_path}")
            except OSError as e:
                show_error(self, "Error", f"Error creating folder {folder_name}.")
                print(f"Error creating folder {new_folder_path}: {e}")
                log.error(f"Error creating folder {new_folder_path}: {e}")
