import sqlite3
from operation.config import settings

conn = sqlite3.connect(settings.DB_PATH)
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print(tables)
conn.close()
