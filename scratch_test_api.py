import sys
import logging
from sara.gui.app.engine import Api
from config import Config
try:
    api = Api(None, None, None, None, None, None, None)
    print("API Instantiated")
    print("Notes:", api.get_notes())
    print("System Stats:", api.get_system_stats())
except Exception as e:
    import traceback
    traceback.print_exc()
