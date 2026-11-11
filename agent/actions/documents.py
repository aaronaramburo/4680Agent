"""Turn an uploaded PDF or Word document into plain text.

The sandbox only ever stores text, so the agent's file tools work the same on
an imported document as on a note. These parsers read untrusted files, so
every loop is bounded and nothing is written to disk here.
"""
import io
import zipfile

from .. import config
from . import ActionError

_SCANNED_HINT = "No selectable text was found. It may be a scan or contain only images."


def extract_text(name: str, data: bytes) -> str:
    extension = name.lower().rsplit(".", 1)[-1]
    text = {"pdf": _pdf_text, "docx": _docx_text}[extension](data)
    if not text.strip():
        raise ActionError(_SCANNED_HINT)
    return text


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ActionError("It is password-protected.")
        if len(reader.pages) > config.MAX_PDF_PAGES:
            raise ActionError(f"It has {len(reader.pages)} pages; the limit is {config.MAX_PDF_PAGES}.")
        parts, size = [], 0
        for number, page in enumerate(reader.pages, 1):
            body = (page.extract_text() or "").strip()
            if body:
                parts.append(f"--- Page {number} ---\n{body}")
                size += len(body)
            _check_length(size)
        return "\n\n".join(parts)
    except ActionError:
        raise
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError):
        raise ActionError("It could not be read as a PDF.") from None


def _docx_text(data: bytes) -> str:
    from docx import Document

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            # A .docx is a zip; refuse one that would expand to something enormous.
            if sum(item.file_size for item in archive.infolist()) > config.MAX_DOCX_UNZIPPED_BYTES:
                raise ActionError("It is too large to open safely.")
        document = Document(io.BytesIO(data))
        parts, size = [], 0
        for paragraph in document.paragraphs:
            line = paragraph.text.strip()
            if not line:
                continue
            style = (paragraph.style.name or "") if paragraph.style is not None else ""
            if style.startswith("Heading"):
                level = style[-1] if style[-1].isdigit() else "1"
                line = "#" * int(level) + " " + line
            elif style.startswith("List"):
                line = "- " + line
            parts.append(line)
            size += len(line)
            _check_length(size)
        for table in document.tables:
            for row in table.rows:
                line = " | ".join(cell.text.strip() for cell in row.cells)
                parts.append(line)
                size += len(line)
                _check_length(size)
        return "\n\n".join(parts)
    except ActionError:
        raise
    except Exception:  # python-docx raises many unrelated types on malformed files
        raise ActionError("It could not be read as a Word document.") from None


def _check_length(size: int):
    if size > config.MAX_FILE_BYTES:
        raise ActionError(f"Its text is longer than the {config.MAX_FILE_BYTES // 1000} KB limit. "
                          "Split it into smaller documents and upload those.")
