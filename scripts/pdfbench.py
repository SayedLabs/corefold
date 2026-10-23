"""Times putting 20 phone photos in one PDF (python scripts/pdfbench.py <folder>)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image  # noqa: E402

from corefold import converters as cv  # noqa: E402
from corefold.core import Ctx, Options  # noqa: E402

d = Path(sys.argv[1])
d.mkdir(parents=True, exist_ok=True)
noise = Image.effect_noise((1008, 756), 60).convert("RGB").resize((4032, 3024))
for i in range(20):
    Image.blend(Image.new("RGB", (4032, 3024), (40 + i * 9, 120, 200 - i * 8)), noise, 0.35).save(d / f"IMG_{i:04}.jpg",
                                                                                                 quality=92)
files = sorted(d.glob("IMG_*.jpg"))
t0 = time.perf_counter()
photos = cv._plain_jpegs(files)
print(f"looking at them: {time.perf_counter() - t0:.2f} s")
for inline in (True, False, True):
    t0 = time.perf_counter()
    cv.jpegs_to_pdf(photos, d / f"out-{inline}.pdf", 150, Ctx(Options()), inline=inline)
    print(f"inline={inline}: {time.perf_counter() - t0:.2f} s")
t0 = time.perf_counter()
cv.write_pages_pdf((cv.flatten(cv.load_image(f)) for f in files), d / "old.pdf", 150)
print(f"the old way: {time.perf_counter() - t0:.2f} s")
