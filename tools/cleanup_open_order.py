import sqlite3
from operation.config import settings

conn = sqlite3.connect(settings.DB_PATH)
conn.execute("DELETE FROM orders WHERE client_order_id LIKE 'testopen_%'")
conn.commit()
conn.close()
print("deleted testopen_*")
