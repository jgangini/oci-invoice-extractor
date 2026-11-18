"""Bound the wall time of one document, including native PDF and ONNX calls."""
import multiprocessing

from .documents import InputError, page_texts
from .fields import extract


class DocumentError(Exception):
    """A permanent document error, safe to record without its contents."""


def _serve(channel):
    try:
        while True:
            request = channel.recv()
            if request is None:
                return
            data, template = request
            try:
                channel.send((True, extract(page_texts(data), template)))
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

    def extract(self, data, template):
        if self.process is None:
            context = multiprocessing.get_context("spawn")
            self.channel, child = context.Pipe()
            self.process = context.Process(target=_serve, args=(child,), daemon=True)
            self.process.start()
            child.close()
        try:
            self.channel.send((data, template))
            if not self.channel.poll(self.seconds):
                raise DocumentError("document_timeout")
            ok, result = self.channel.recv()
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

