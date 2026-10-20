"""Dedicated WSGI entry: never imports app.py or opens the schedule database."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from pixel_canvas import create_app

application = create_app()
