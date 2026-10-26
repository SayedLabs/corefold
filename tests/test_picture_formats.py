"""More picture formats in the Converter: a camera's RAW file is read through the full-size JPEG inside it
(turned the way the camera was held, never its small thumbnail), a drawing (SVG) is drawn sharp on a
see-through background, and pictures can now be saved as TIFF (packed, nothing lost) and GIF (moving ones
keep moving)."""
import io
import threading

from PIL import Image

from corefold import converters
from corefold.core import Ctx, Options, category


def _jpeg(size, colour):
    b = io.BytesIO()
    Image.new("RGB", size, colour).save(b, "JPEG", quality=80)
    return b.getvalue()


def _camera_file(path, turn=6):
    """Like a camera's RAW file: TIFF-style details first, then a small and a full-size JPEG among the
    sensor's own data."""
    exif = Image.Exif()
    exif[0x0112] = turn
    head = exif.tobytes()[6:]  # (without "Exif\0\0": the file starts like a TIFF)
    path.write_bytes(head + b"\0" * 300 + _jpeg((160, 120), "blue") + bytes(range(256)) * 40
                     + _jpeg((1200, 800), "red") + b"\x55" * 5000)
    return path


def _ctx():
    return Ctx(Options(), lambda f: None, threading.Event(), {})


def test_a_camera_file_is_its_full_size_picture_turned_upright(tmp_path):
    raw = _camera_file(tmp_path / "IMG_0001.CR2")
    converters.setup_pillow()
    with Image.open(raw) as im:
        assert im.format == "RAW" and im.size == (1200, 800)
    im = converters.load_image(raw)
    assert im.size == (800, 1200) and im.getpixel((400, 600))[0] > 200  # (red, turned)
    out = converters.convert_image(raw, _ctx(), ext=".jpg").outputs[0]
    assert out.name == "IMG_0001.jpg"
    with Image.open(out) as done:
        assert done.size == (800, 1200)
    assert category(raw) == "image" and converters.keep_format(raw) == ".jpg"


def test_a_tiff_is_still_read_as_a_tiff(tmp_path):
    p = tmp_path / "scan.tif"
    Image.new("L", (50, 40), 128).save(p)
    converters.setup_pillow()
    with Image.open(p) as im:
        assert im.format == "TIFF" and im.size == (50, 40)


def test_a_drawing_becomes_a_sharp_picture(tmp_path, qapp):
    svg = tmp_path / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="24">'
                   '<rect x="0" y="0" width="24" height="24" fill="#00ff00"/></svg>')
    out = converters.convert_image(svg, _ctx(), ext=".png").outputs[0]
    with Image.open(out) as im:
        assert im.size == (2400, 1200) and im.mode == "RGBA"  # (an icon-sized drawing made sharp)
        assert im.getpixel((10, 10))[1] > 200 and im.getpixel((2000, 600))[3] == 0  # (see-through beside it)
    assert category(svg) == "image"


def test_save_as_tiff_and_gif(tmp_path):
    src = tmp_path / "photo.png"
    Image.new("RGBA", (300, 200), (10, 200, 30, 255)).save(src)
    tif = converters.convert_image(src, _ctx(), ext=".tiff").outputs[0]
    with Image.open(tif) as im:
        assert im.format == "TIFF" and im.info.get("compression") == "tiff_adobe_deflate"
    gif = converters.convert_image(src, _ctx(), ext=".gif").outputs[0]
    with Image.open(gif) as im:
        assert im.format == "GIF" and im.size == (300, 200)
    keys = [t.key for t in converters.catalog()["image"]]
    assert "image:tiff" in keys and "image:gif" in keys


def test_a_moving_webp_stays_moving_as_a_gif(tmp_path):
    src = tmp_path / "wave.webp"
    frames = [Image.new("RGB", (40, 40), c) for c in ("red", "green", "blue")]
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=100, loop=0)
    out = converters.convert_image(src, _ctx(), ext=".gif").outputs[0]
    with Image.open(out) as im:
        assert im.format == "GIF" and im.n_frames == 3
