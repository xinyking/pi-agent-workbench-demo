import re
from pathlib import Path
from ..store import TERMINAL
STATIC = Path(__file__).resolve().parents[1] / 'static'
ID = re.compile('^[a-f0-9]{32}$')
STREAM_TERMINAL = TERMINAL | {'awaiting_confirmation'}
