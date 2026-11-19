"""Bound the wall time of one document, including native PDF and ONNX calls."""
import multiprocessing
import logging
import time

from .documents import InputError, page_texts, recognize
from .fields import extract
from .geometry import ocr_lines, preview_pages


class DocumentError(Exception):
    """A permanent document error, safe to record without its contents."""


def _extract_document(data, template, channel=None):
    contacted_ocr = False
    geometry = {}

    def ocr(content, index):
        nonlocal contacted_ocr
        if channel is not None and not contacted_ocr:
            channel.send(("stage", "OCR"))
            contacted_ocr = True
        def capture(lines, width, height):
            geometry[index + 1] = ocr_lines(lines, width, height)
        return recognize(content, index, capture=capture)

    def pages():
        yield from page_texts(data, ocr=ocr)
        if channel is not None:
            channel.send(("stage", "EXTRACT_FIELDS"))

    fields = extract(pages(), template, geometry)
    if channel is None:
        return fields
    channel.send(("stage", "LOCATE_EVIDENCE"))
    return fields, preview_pages(data, fields, geometry)


def _serve(channel):
    # Parser diagnostics can quote bytes from uploaded documents.
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
    try:
        while True:
            request = channel.recv()
            if request is None:
                return
            data, template, track = request
            try:
                result = _extract_document(data, template, channel if track else None)
                channel.send((True, result))
            except InputError as exc:
                channel.send((False, str(exc)))
            except Exception as exc:
                channel.send((False, type(exc).__name__))
    except EOFError:
        pass
    finally:
        channel.close()


class DocumentWorker:
    def __init__(self, seconds=150):
        self.seconds = seconds
        self.process = None
        self.channel = None

    def extract(self, data, template, on_stage=None):
        if self.process is None:
            context = multiprocessing.get_context("spawn")
            self.channel, child = context.Pipe()
            self.process = context.Process(target=_serve, args=(child,), daemon=True)
            self.process.start()
            child.close()
        try:
            self.channel.send((data, template, on_stage is not None))
            deadline = time.monotonic() + self.seconds
            while self.channel.poll(max(0, deadline - time.monotonic())):
                ok, result = self.channel.recv()
                if ok != "stage":
                    break
                on_stage(result)
            else:
                raise DocumentError("document_timeout")
        except (EOFError, OSError) as exc:
            self.close()
            raise DocumentError("document_worker_exit") from exc
        except DocumentError:
            self.close()
            raise
        if not ok:
            raise DocumentError(result)
        return result

    def close(self):
        if self.process is not None:
            self.process.terminate()
            self.process.join(timeout=3)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
            self.channel.close()
            self.process.close()
            self.process = None

