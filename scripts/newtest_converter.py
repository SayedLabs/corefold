"""Checks the Converter with Corefold's own FFmpeg on a real Windows PC, before the first launch: videos that are
already H.264 are copied into MP4 instead of made again (and only those), a song keeps its album cover when it's
trimmed, sped up or made louder, a preset's name can't break the new file's name, Small keeps a GIF moving and a
logo see-through, awkward file names work, and files that only pretend to be videos are turned away.

Run from the repo root: python scripts/newtest_converter.py
Prints one line per check and "newtest_converter: N problem(s)"."""
import faulthandler
import json
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
faulthandler.dump_traceback_later(600, exit=True)  # stuck somewhere: say where, and stop
problems = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        problems.append(what)
    return ok


from PIL import Image  # noqa: E402

from corefold import converters as cv, presets  # noqa: E402
from corefold.core import Options, clear_cache  # noqa: E402

FF = cv.find_tool("ffmpeg")
PROBE = cv.find_tool("ffprobe")
print("FFmpeg:", FF, flush=True)
WORK = Path(tempfile.mkdtemp(prefix="corefold-convtest-"))
SRC, OUT = WORK / "src", WORK / "out"
SRC.mkdir()
OUT.mkdir()


def ff(*args):
    r = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode:
        print("   (ffmpeg:", r.stderr.strip()[-300:], ")")
    return r.returncode == 0


def streams(path: Path) -> list[dict]:
    r = subprocess.run([PROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt:stream_disposition"
                        "=attached_pic", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout or "{}").get("streams", [])


def video_md5(path: Path) -> str:
    r = subprocess.run([FF, "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"],
                       capture_output=True, text=True)
    return r.stdout.strip()


TOOLS = {}


def tools():
    TOOLS.clear()
    TOOLS.update({t.key: t for kind in cv.catalog().values() for t in kind})


def run(key, files, options=None, **params):
    options = options or Options(output_folder=OUT)
    started = time.monotonic()
    result, errors = cv.run_job(TOOLS[key], [SRC / f if isinstance(f, str) else f for f in files], options,
                                params=params)
    took = time.monotonic() - started
    print(f"      {key} {[str(f) for f in files]} -> {[p.name for p in result.outputs]} {errors} ({took:.1f} s)")
    return result, errors, took


def media():
    sub = SRC / "subs.srt"
    sub.write_text("1\n00:00:00,500 --> 00:00:02,000\nمرحبا Hello\n", "utf-8")
    long = ["-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20", "-f", "lavfi", "-i", "sine=f=440:d=20"]
    ok = ff(*long, "-i", str(sub), "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "srt", "-shortest", str(SRC / "obs recording.mkv"))
    ok &= ff(*long, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             "-t", "3", str(SRC / "phone.mov"))
    ok &= ff(*long, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-shortest",
             "-t", "3", str(SRC / "opus sound.mkv"))
    ok &= ff(*long, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "libmp3lame", "-shortest",
             "-t", "3", str(SRC / "old.avi"))
    ff(*long, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p10le", "-c:a", "aac", "-shortest", "-t", "3",
       str(SRC / "ten bit.mkv"))  # (only if this FFmpeg's x264 makes 10-bit)
    ok &= ff("-f", "lavfi", "-i", "sine=d=5", "-f", "lavfi", "-i", "color=red:s=300x300:d=1", "-map", "0", "-map", "1",
             "-c:a", "libmp3lame", "-c:v", "mjpeg", "-disposition:v", "attached_pic", "-id3v2_version", "3",
             str(SRC / "song.mp3"))
    for name in ("-start.mp4", "a'b%c;[x],y=z.mp4", "فيديو عربي.mp4"):
        ok &= ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=3", "-f", "lavfi", "-i", "sine=d=3", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(SRC / name))
    shutil.copy(sub, SRC / "a'b%c;[x],y=z.srt")
    (SRC / "playlist.mp4").write_text("#EXTM3U\n#EXTINF:1.0,\nC:\\Windows\\win.ini\n#EXT-X-ENDLIST\n")
    (SRC / "list.mp4").write_text("ffconcat version 1.0\nfile C:\\Windows\\win.ini\n")
    (SRC / "empty.mp4").write_bytes(b"")
    (SRC / "words.mp4").write_text("hello")
    frames = [Image.new("RGBA", (800, 600), (i * 40, 90, 200, 255)) for i in range(5)]
    frames[0].save(SRC / "moving.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    pics = SRC / "Pictures"
    pics.mkdir()
    Image.new("RGBA", (400, 400), (0, 0, 0, 0)).save(pics / "logo.png")
    Image.new("RGB", (3000, 2000), (20, 120, 200)).save(pics / "photo.jpg")
    return check(ok, "test files made with Corefold's FFmpeg")


def fast_copy():
    tools()
    r, errs, took = run("video:mp4", ["obs recording.mkv"])
    out = r.outputs[0] if r.outputs else None
    check(out is not None and not errs, "an OBS recording (MKV, H.264 + AAC) becomes an MP4")
    if out:
        check(video_md5(out) == video_md5(SRC / "obs recording.mkv"),
              f"its picture is copied as it is, not made again ({took:.1f} s)")
        kinds = [s["codec_type"] + ":" + s["codec_name"] for s in streams(out)]
        check("subtitle:mov_text" in kinds and "audio:aac" in kinds, f"its sound and subtitles come along ({kinds})")
    r, errs, _ = run("video:mp4", ["phone.mov"])
    check(r.outputs and video_md5(r.outputs[0]) == video_md5(SRC / "phone.mov"), "an iPhone-style MOV is copied too")
    r, errs, _ = run("video:mp4", ["opus sound.mkv"])
    check(r.outputs and video_md5(r.outputs[0]) != video_md5(SRC / "opus sound.mkv")
          and any(s["codec_name"] == "aac" for s in streams(r.outputs[0])),
          "Opus sound (MP4 players can't all play it) means the video is made again, with AAC")
    r, errs, _ = run("video:mp4", ["old.avi"])
    check(r.outputs and video_md5(r.outputs[0]) != video_md5(SRC / "old.avi"), "an AVI is made again (its times can be off)")
    if (SRC / "ten bit.mkv").exists():
        r, errs, _ = run("video:mp4", ["ten bit.mkv"])
        check(r.outputs and any(s.get("pix_fmt") == "yuv420p" for s in streams(r.outputs[0])),
              "a 10-bit video is made again as 8-bit (TVs and phones can't play 10-bit H.264)")
    smaller = Options(output_folder=OUT, video_crf=28)
    r, errs, _ = run("video:mp4", ["obs recording.mkv"], smaller)
    check(r.outputs and r.outputs[0].stat().st_size < (SRC / "obs recording.mkv").stat().st_size * 0.9,
          "with 'smaller file' in Settings it's made again, smaller")
    r, errs, _ = run("video:mp4", ["obs recording.mkv"], Options(output_folder=OUT, video_max=480))
    check(r.outputs and video_md5(r.outputs[0]) != video_md5(SRC / "obs recording.mkv"),
          "with a size limit smaller than the video it's made again")


def album_cover():
    tools()
    for key, params in (("audio:trim", {"start": 1, "end": 3}), ("audio:speed", {"speed": 1.5}),
                        ("audio:volume", {})):
        r, errs, _ = run(key, ["song.mp3"], **params)
        covers = [s for s in streams(r.outputs[0]) if (s.get("disposition") or {}).get("attached_pic")] \
            if r.outputs else []
        check(r.outputs and not errs and covers, f"{key}: the song keeps its album cover")


def preset_name():
    presets.use([{"id": "ab12", "name": "Phone: small/v2", "kind": "audio", "format": ".m4a", "quality": "smaller"},
                 {"id": "cd34", "name": "CON", "kind": "video", "format": ".mp4", "quality": "smaller", "size": "720"}])
    tools()
    r, errs, _ = run("preset:ab12", ["song.mp3"])
    check(r.outputs and r.outputs[0].parent == OUT and r.outputs[0].exists() and ":" not in r.outputs[0].name,
          f"a preset called 'Phone: small/v2' makes a normal, visible file ({[p.name for p in r.outputs]} {errs})")
    r, errs, _ = run("preset:cd34", ["phone.mov"])
    check(r.outputs and r.outputs[0].exists(), f"a preset called 'CON' still works ({errs})")
    presets.use([])
    tools()


def small():
    tools()
    r, errs, _ = run("image:small", ["moving.gif"])
    if check(r.outputs and r.outputs[0].suffix == ".gif", "Small on an animated GIF makes a GIF"):
        with Image.open(r.outputs[0]) as im:
            check(getattr(im, "n_frames", 1) == 5, "and it still moves (all 5 frames)")
    r, errs, _ = run("folder:small", [SRC / "Pictures"])
    made = sorted(p.name for p in r.outputs[0].rglob("*")) if r.outputs else []
    check(made == ["logo.png", "photo.jpg"], f"folder Small keeps the see-through logo a PNG ({made})")


def names_and_fakes():
    tools()
    for name in ("-start.mp4", "a'b%c;[x],y=z.mp4", "فيديو عربي.mp4"):
        r, errs, _ = run("video:mp3", [name])
        check(r.outputs and not errs, f"MP3 from {name!r}")
    r, errs, _ = run("video:subtitles", ["a'b%c;[x],y=z.mp4"], how="burn")
    check(r.outputs and not errs, "subtitles drawn into a video with ' % ; [ ] , = in its name")
    r, errs, _ = run("video:frames", ["a'b%c;[x],y=z.mp4"], mode="count", count=3)
    check(r.outputs and len(list(r.outputs[0].glob("*.png"))) == 3, "3 pictures from that video")
    for name in ("playlist.mp4", "list.mp4", "empty.mp4", "words.mp4"):
        r, errs, _ = run("video:mp4", [name])
        check(not r.outputs and len(errs) == 1, f"{name} is turned away with a message, nothing made ({errs})")
    leftovers = [p.name for p in OUT.iterdir() if p.stem in ("playlist", "list", "empty", "words")]
    check(not leftovers, f"no half-made files left behind ({leftovers})")


def logic():
    from corefold.dialogs import parse_time
    check(parse_time("1:02:03.5") == 3723.5 and parse_time("2:03.5") == 123.5 and parse_time("75") == 75,
          "Trim's times can be typed as 1:02:03.5, 2:03.5 or seconds")
    check(parse_time("1:75") is None and parse_time("a") is None and parse_time("") is None, "and nonsense isn't taken")
    from corefold.wheel import GROUPS
    check(all(GROUPS[a] != "archive" for a in ("merge", "split", "lock", "unlock")),
          "a PDF's Merge, Split, Lock and Unlock aren't listed under Archive")


for part in (media, fast_copy, album_cover, preset_name, small, names_and_fakes, logic):
    try:
        clear_cache()
        part()
    except Exception:
        traceback.print_exc()
        check(False, f"{part.__name__} ran to the end")

shutil.rmtree(WORK, ignore_errors=True)
print(f"newtest_converter: {len(problems)} problem(s)", flush=True)
sys.exit(1 if problems else 0)
