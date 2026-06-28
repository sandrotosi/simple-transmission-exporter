import os
import sys
from pathlib import Path

# Make the single-module exporter importable from the tests.
sys.path.insert(0, str(Path(__file__).parent))

# The module validates these required vars at import time; provide dummies so it
# can be imported without a real Transmission. Tests mock the RPC client.
os.environ.setdefault('TRANSMISSION_HOST', 'localhost')
os.environ.setdefault('TRANSMISSION_PORT', '9091')
os.environ.setdefault('TRANSMISSION_USERNAME', 'user')
os.environ.setdefault('TRANSMISSION_PASSWORD', 'pass')
