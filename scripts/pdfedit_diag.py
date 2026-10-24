import io, sys, os
sys.path.insert(0, os.getcwd())
sys.stdout.reconfigure(encoding="utf-8")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from corefold import pdfedit
from pypdf import PdfReader
import pypdfium2 as pdfium
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
TEXT = "نص عربي جديد"
raw = None
orig = pdfedit._arabic_unicode
def spy(data, typed):
    global raw
    raw = data
    return orig(data, typed)
pdfedit._arabic_unicode = spy
data = pdfedit._layer({0: [dict(kind="text", x=10, y=10, text=TEXT, font="Arial", size=14, color="#000000")]}, {0: (200, 100)})
for name, d in (("raw", raw), ("fixed", data)):
    r = PdfReader(io.BytesIO(d))
    for k, f in r.pages[0]["/Resources"]["/Font"].items():
        f = f.get_object()
        print("==", name, k, f.get("/BaseFont"))
        tu = f.get("/ToUnicode")
        print(tu.get_object().get_data().decode("latin-1") if tu else "no ToUnicode")
    doc = pdfium.PdfDocument(d)
    print(name, "pdfium:", [hex(ord(ch)) for ch in doc[0].get_textpage().get_text_range()])
print("SEARCHABLE:", "جديد" in pdfium.PdfDocument(data)[0].get_textpage().get_text_range())
