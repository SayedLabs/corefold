"""Small conveniences from the feature review: Copy the text on a scan offers Make searchable, page
numbers can start at any number, photos and videos are just stored in a ZIP (much faster, hardly
bigger), and Done in Settings goes back to the page that was open instead of closing the window."""
import zipfile

from PIL import Image
from pypdf import PdfWriter

from corefold import archives, converters, pdfstamp
from corefold.core import Ctx, Options


def test_a_scan_with_no_text_offers_make_searchable(tmp_path):
    w = PdfWriter()
    w.add_blank_page(300, 400)
    w.write(tmp_path / "scan.pdf")
    tool = next(t for t in converters.catalog()["pdf"] if t.key == "pdf:text")
    result, errors = converters.run_job(tool, [tmp_path / "scan.pdf"], Options())
    assert not errors and not result.outputs and result.scans == [tmp_path / "scan.pdf"]


def test_page_numbers_can_start_at_any_number(tmp_path):
    import pypdfium2 as pdfium
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(300, 400)
    w.write(tmp_path / "chapter.pdf")
    res = pdfstamp.page_numbers(tmp_path / "chapter.pdf", Ctx(Options(), params={"style": "of", "start": 12}))
    doc = pdfium.PdfDocument(str(res.outputs[0]))
    try:
        texts = []
        for i in range(len(doc)):
            tp = doc[i].get_textpage()
            texts.append(tp.get_text_bounded().strip())
            tp.close()
    finally:
        doc.close()
    assert texts == ["12 / 14", "13 / 14", "14 / 14"]


def test_photos_are_stored_as_they_are(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "find_7z", lambda: None)  # (Corefold's own ZIP, as without 7-Zip)
    Image.new("RGB", (400, 300), (10, 120, 200)).save(tmp_path / "a.jpg")
    (tmp_path / "notes.txt").write_text("words " * 2000)
    archives.pack_into([tmp_path / "a.jpg"], tmp_path / "photos.zip", Ctx(Options()))
    archives.pack_into([tmp_path / "a.jpg", tmp_path / "notes.txt"], tmp_path / "mixed.zip", Ctx(Options()))
    with zipfile.ZipFile(tmp_path / "photos.zip") as z:
        assert [i.compress_type for i in z.infolist()] == [zipfile.ZIP_STORED]
    with zipfile.ZipFile(tmp_path / "mixed.zip") as z:  # (anything else in it: squeezed as before)
        assert {i.compress_type for i in z.infolist()} == {zipfile.ZIP_DEFLATED}
    assert archives.packed_already([tmp_path / "a.jpg"]) and not archives.packed_already([tmp_path])


def test_7zip_stores_photos(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(archives, "find_7z", lambda: "7z")
    monkeypatch.setattr(archives, "_run7z", lambda args, ctx, cwd=None: (seen.append(args), (0, ""))[1])
    Image.new("RGB", (40, 30)).save(tmp_path / "a.jpg")
    archives.pack_into([tmp_path / "a.jpg"], tmp_path / "p.7z", Ctx(Options()), fmt="7z")
    assert "-mx=0" in seen[0]
    archives.pack_into([tmp_path / "a.jpg"], tmp_path / "q.7z", Ctx(Options()), fmt="7z", level="max")
    assert "-mx=9" in seen[1]  # (Max still squeezes everything)


def test_done_in_settings_goes_back(qtbot):
    from PySide6.QtGui import QIcon

    from corefold import app as A
    from corefold import settings as settings_mod
    s = settings_mod.Settings.load()
    s.first_run = False
    s.tour_done = True
    s.animations = False
    s.tools_off = []
    s.last_page = "Home"
    s.save()
    c = A.Controller(A.QApplication.instance(), QIcon())
    c.apply_tools()
    w = c.window
    try:
        w.show()
        w.open_page("Files")
        w.open_page("General")
        w.settings_done()
        assert w.isVisible() and w.current_page() == "Files"
        w._went.clear()
        w.open_page("General")
        w._went.clear()
        w.settings_done()
        assert w.current_page() == "Home"
    finally:
        from PySide6.QtCore import QTimer
        from shiboken6 import delete
        c.tray.hide()
        for timer in c.findChildren(QTimer):
            timer.stop()
        w.close()
        delete(w)
        delete(c)


def test_more_sound_and_video_kinds_are_known():
    from pathlib import Path

    from corefold.core import category
    for name, kind in (("voice.amr", "audio"), ("book.m4b", "audio"), ("camcorder.mts", "video"),
                       ("camcorder.m2ts", "video"), ("VTS_01_1.VOB", "video")):
        assert category(Path(name)) == kind, name


def test_resize_never_makes_a_picture_bigger(tmp_path):
    Image.new("RGB", (400, 300)).save(tmp_path / "small.png")
    resize = lambda **p: converters.resize_image(tmp_path / "small.png", Ctx(Options(), params={"preset": "custom", **p}))  # noqa: E731
    with Image.open(resize(width=1200).outputs[0]) as im:
        assert im.size == (400, 300)
    with Image.open(resize(width=800, bigger=True).outputs[0]) as im:
        assert im.size == (800, 600)


def test_resize_shows_the_size_as_it_looks(tmp_path):
    from corefold.dialogs import picture_size
    im = Image.new("RGB", (400, 300))
    exif = im.getexif()
    exif[0x0112] = 6  # (a phone photo taken upright)
    im.save(tmp_path / "upright.jpg", exif=exif)
    assert picture_size(tmp_path / "upright.jpg") == (300, 400)
    assert picture_size(tmp_path / "missing.jpg") is None
