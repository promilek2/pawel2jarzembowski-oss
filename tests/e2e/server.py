"""Serwer testowy panelu z atrapą NFZ (dane fikcyjne). Do e2e i pracy nad frontendem.

Uruchomienie ręczne:  python -m tests.e2e.server  →  http://127.0.0.1:8765
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from termin.config import Settings
from termin.runtime import Transports
from termin.web.app import create_app
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz
from tests.fake_services import FakeNotifyServices


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def running_server(port: int | None = None) -> Iterator[str]:
    port = port or free_port()
    with TemporaryDirectory() as tmp:
        settings = Settings(
            data_dir=Path(tmp),
            geocoder="offline",
            tile_url="/static/icons/icon-192.png?{z}{x}{y}",  # bez sieci: zaślepka kafelków
            rate_limit="1000/minute",
        )
        app = create_app(
            settings,
            transports=Transports(
                nfz=FakeNfz.with_defaults().transport(),
                notify=FakeNotifyServices().transport(),
            ),
            today=lambda: TODAY,
        )
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("serwer testowy nie wystartował")
            time.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    with running_server(8765) as url:
        print(f"Panel z danymi fikcyjnymi: {url}  (Ctrl+C kończy)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
