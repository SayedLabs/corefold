"""Checks for fixes from the 1.10 review: Viewer, Capture, Player subtitles, Cloud folders and the File Organizer."""
import time

from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from corefold import cloud, organizer
from corefold.core import legacy_text


def test_old_code_page_subtitles_keep_their_letters():
    assert legacy_text("Año próximo, canción".encode("cp1252")) == "Año próximo, canción"
    arabic = "1\r\n00:00:01,000 --> 00:00:02,000\r\nمرحبا بكم في البرنامج\r\n"
    assert legacy_text(arabic.encode("cp1256")) == arabic


def test_light_and_colour_keep_the_photo_details():
    from corefold.viewer import _adjusted_pil
    im = Image.new("RGB", (8, 8), "gray")
    im.info["icc_profile"] = b"p" * 40
    im.info["exif"] = b"Exif\x00\x00details"
    out = _adjusted_pil(im, (10, 0, 0, False))
    assert out.info.get("icc_profile") == im.info["icc_profile"] and out.info.get("exif") == im.info["exif"]


def test_see_through_picture_zoomed_in_draws_quickly(qtbot):
    from corefold.viewer import PictureView
    view = PictureView(dark=True)
    qtbot.addWidget(view)
    view.resize(800, 600)
    big = QImage(4000, 4000, QImage.Format.Format_ARGB32)
    big.fill(0)
    view.set_image(big)
    view.show()
    view.set_zoom(16.0)
    start = time.monotonic()
    view.repaint()
    assert time.monotonic() - start < 2  # (square by square it took millions of calls)


def test_word_clicks_forget_words_read_before(qtbot):
    from corefold.capview import Canvas, TextLayer
    image = QImage(200, 100, QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    canvas = Canvas(image)
    qtbot.addWidget(canvas)
    canvas._clicks = (time.monotonic(), 40, 1)
    canvas.set_text(TextLayer([]))
    assert canvas._clicks[1] == -1


def test_folder_copied_into_itself_stops(tmp_path):
    (tmp_path / "A" / "B").mkdir(parents=True)
    (tmp_path / "A" / "f.txt").write_text("x")
    (tmp_path / "A" / "B" / "g.txt").write_text("y")
    acc = cloud.Account("onedrive", "", extra={"local": str(tmp_path)})
    c = cloud.client(acc)
    assert cloud.upload_tree(c, tmp_path / "A", c._entry(tmp_path / "A")) == 2
    assert cloud.upload_tree(c, tmp_path / "A", c._entry(tmp_path / "A" / "B")) == 4  # (A's copy from before too)
    deepest = max(len(p.relative_to(tmp_path).parts) for p in tmp_path.rglob("*"))
    assert deepest < 8


def test_sorts_and_undo_take_turns():
    assert organizer._one_at_a_time.acquire(timeout=1)
    organizer._one_at_a_time.release()


def test_snip_stays_on_its_screen(qtbot):
    from PySide6.QtCore import QPoint
    from corefold.capture import Snip

    class Fake:
        start, end = QPoint(10, 10), QPoint(5000, 5000)

        def rect(self):
            return QRectF(0, 0, 800, 600).toRect()
    r = Snip.selection(Fake())
    assert r is not None and r.right() < 800 and r.bottom() < 600
