import os
import sys

APPLICATION_ROOT = os.path.dirname(os.path.abspath(__file__))
if APPLICATION_ROOT not in sys.path:
    sys.path.insert(0, APPLICATION_ROOT)

from private.app.bootstrap import launch_app


if __name__ == "__main__":
    launch_app()
