"""The PDF editor on a window that isn't wide (a 1024-wide screen, or a window not made full size): the list
of pages comes first, and the options only take room the page can spare."""
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("width", [600, 700, 730, 760, 800, 860, 1100])
def test_pages_first_then_options(qapp, width):
    from corefold import pdfeditor
    ed = pdfeditor.PdfEditor(MagicMock())
    ed.insp_needed = True  # (something selected: the options have something to set)
    ed.show()
    ed.resize(width, 700)
    qapp.processEvents()
    assert ed.width() == width
    assert ed.side.isVisible() is (width >= 720)  # (on a 1024-wide screen the editor is about 800 wide)
    left = width - (ed.side.sizeHint().width() if ed.side.isVisible() else 0)
    if width < 860:
        assert ed.inspector.isVisible() is (left - pdfeditor.INSPECTOR_WIDTH >= 320)
    else:
        assert ed.inspector.isVisible()
    ed.close()
