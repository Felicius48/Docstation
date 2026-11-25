import fcntl
import time
from threading import Event, Thread

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from documents.jobs import claim, process, recover
from documents.previews import expire_previews, process_preview, recover_previews


class Command(BaseCommand):
    help = "Process the conversion queue with a single worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process queued jobs and exit.")

    def handle(self, *args, **options):
        directory = settings.DATA_DIR / "work"
        with (directory / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CommandError("A worker is already running.") from exc
            heartbeat = directory / "worker.heartbeat"
            heartbeat.touch()
            stop = Event()

            def pulse():
                while not stop.wait(5):
                    heartbeat.touch()

            thread = Thread(target=pulse, daemon=True)
            thread.start()
            try:
                self.run_queue(directory, options["once"])
            finally:
                stop.set()
                thread.join()
                heartbeat.unlink(missing_ok=True)

    def run_queue(self, directory, once):
        recover(directory)
        recover_previews(directory)
        next_cleanup = 0
        self.stdout.write("Docstation worker ready.")
        while True:
            close_old_connections()
            if time.monotonic() >= next_cleanup:
                expire_previews()
                next_cleanup = time.monotonic() + 60
            job = claim()
            if job is not None:
                process(job)
            elif process_preview():
                continue
            elif once:
                return
            else:
                time.sleep(1)
