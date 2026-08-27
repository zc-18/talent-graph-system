"""pytest 配置与共享 fixture。"""
import os
import sys

# Test collection imports ``app.main`` in several modules. Pin the process to an
# isolated SQLite database before any application module can construct its engine.
os.environ["DATABASE_URL_OVERRIDE"] = "sqlite://"
os.environ["READ_ONLY"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
