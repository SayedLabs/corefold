"""Converter and archive fixes from the 1.10 review: every page and frame kept, scans not blown up by
Compress, folder conversions never overwrite, Outlook emoji, mail header rows and split-archive parts."""
import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from corefold import archives, converters, extras, mail, moretools
from corefold.core import Ctx, Options


def ctx(**params):
    return Ctx(Options(), params=params)


def test_multipage_tiff_keeps_every_page(tmp_path):
    src = tmp_path / "fax.tiff"
    pages = [Image.new("RGB", (40, 30), c) for c in ("red", "green", "blue")]
    pages[0].save(src, save_all=True, append_images=pages[1:])
    r = converters.convert_image(src, ctx(), ".png")
    assert len(r.outputs) == 3 and all(p.exists() for p in r.outputs)
    assert Image.open(r.outputs[2]).getpixel((0, 0))[:3] == (0, 0, 255)


def test_animated_gif_to_webp_stays_animated(tmp_path):
    src = tmp_path / "moving.gif"
    frames = [Image.new("RGB", (32, 32), c) for c in ("red", "green", "blue")]
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=100, loop=0)
    out = converters.convert_image(src, ctx(), ".webp").outputs[0]
    with Image.open(out) as im:
        assert getattr(im, "n_frames", 1) == 3


def _scan_pdf(path: Path):
    im = Image.new("1", (1200, 1600), 1)
    d = ImageDraw.Draw(im)
    for y in range(0, 1600, 40):
        d.text((50, y), "Lorem ipsum dolor sit amet " * 3, fill=0)
    im.save(path, resolution=150)


def test_compress_leaves_black_and_white_scans_alone(tmp_path):
    src, out = tmp_path / "scan.pdf", tmp_path / "out.pdf"
    _scan_pdf(src)
    converters.write_compressed_pdf(src, out, ctx())
    from pypdf import PdfReader
    assert [img.image.mode for img in PdfReader(str(out)).pages[0].images] == ["1"]
    assert out.stat().st_size <= src.stat().st_size * 1.1


def test_fit_pdf_never_hands_back_a_bigger_file(tmp_path):
    src = tmp_path / "scan.pdf"
    _scan_pdf(src)
    r = extras.fit(src, ctx(mb=0.001))
    assert all(p.stat().st_size < src.stat().st_size for p in r.outputs)


def test_folder_conversion_keeps_files_with_the_same_name(tmp_path):
    folder = tmp_path / "pics"
    folder.mkdir()
    Image.new("RGB", (20, 20), "red").save(folder / "a.png")
    Image.new("RGB", (20, 20), "blue").save(folder / "a.jpg")
    Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(folder / "clear.png")
    outdir = moretools.convert_folder(folder, ctx(), fmt="small").outputs[0]
    assert len(list(outdir.glob("a*.jpg"))) == 2
    # (a see-through picture stays a see-through PNG, like the Small tool makes it)
    assert not (outdir / "clear.jpg").exists() and Image.open(outdir / "clear.png").getpixel((5, 5))[3] == 0


def test_outlook_emoji_is_one_character_and_saves_as_eml():
    kind, body = mail.rtf_body(rb"{\rtf1\ansi\ansicpg1252\fromhtml1 {\*\htmltag <p>}Hi \u-10179?\u-8704? {\*\htmltag </p>}}")
    assert kind == "html" and "\U0001f600" in body
    assert b"Hi" in mail.to_eml(mail.Message(subject="x", html=body))


def test_mail_header_rows_show_as_formatted_text(qtbot, tmp_path):
    from unittest import mock

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QLabel

    from corefold.mailview import MailWindow
    msg = mail.Message(subject="Hello", sender="Ann <ann@example.com>", text="hi")
    win = MailWindow(mock.MagicMock(), tmp_path / "x.eml", QIcon(), msg)
    qtbot.addWidget(win)
    rows = [lb for lb in win.findChildren(QLabel) if "ann@example.com" in lb.text()]
    assert rows and all(lb.textFormat() == Qt.RichText for lb in rows)


def test_packing_never_touches_an_older_split_archive(tmp_path):
    folder = tmp_path / "Backup"
    folder.mkdir()
    (folder / "note.txt").write_text("hello")
    old = [tmp_path / "Backup.zip.001", tmp_path / "Backup.zip.002"]
    for p in old:
        p.write_bytes(b"older archive")
    out = archives.pack([folder], ctx(), fmt="zip").outputs[0]
    assert out.name == "Backup (2).zip" and zipfile.is_zipfile(out)
    assert all(p.read_bytes() == b"older archive" for p in old)


def test_tar_names_windows_refuses_are_made_safe():
    assert archives.windows_safe_name("docs/what?.txt") == "docs/what_.txt"
    assert archives.windows_safe_name("a:b/c*d|e ") == "a_b/c_d_e"
    assert archives.windows_safe_name("x/../y") == "x/../y"


def test_video_frames_with_percent_in_the_name(tmp_path):
    try:
        exe = converters.find_tool("ffmpeg")
    except RuntimeError:
        pytest.skip("no FFmpeg here")
    import subprocess
    src = tmp_path / "promo 50% off.mp4"
    subprocess.run([exe, "-v", "error", "-f", "lavfi", "-i", "testsrc=d=2:s=64x48:r=5", "-pix_fmt", "yuv420p",
                    str(src)], check=True, stdin=subprocess.DEVNULL)
    r = extras.video_frames(src, ctx(mode="every", seconds=1))
    assert len(list(r.outputs[0].glob("*.png"))) >= 2
    shutil.rmtree(r.outputs[0], ignore_errors=True)
