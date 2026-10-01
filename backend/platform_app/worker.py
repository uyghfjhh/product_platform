"""Explicit worker process bootstrap; importing this module does not start it."""

from huey.consumer import Consumer

from .config import load_settings
from .queue import create_queue


def main():
    huey, _execute = create_queue(load_settings())
    Consumer(huey, workers=2, worker_type="thread", max_delay=0.5).run()


if __name__ == "__main__":
    main()
