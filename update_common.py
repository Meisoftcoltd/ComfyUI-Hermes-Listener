import sqlite3
import json
from collections import defaultdict, Counter

db_path = "/home/meisoft/ComfyUI-Hermes-Listener/comfyui_nodes.db"
db_conn = sqlite3.connect(db_path)
db_c = db_conn.cursor()

# Get connections
db_c.execute("SELECT source_class_type, target_class_type FROM node_connections")
conns = list(db_c.fetchall())

# Build adjacency counter
node_common = defaultdict(lambda: Counter())
for conn in conns:
    s, t = conn  # tuple from fetchall()
    node_common[s][t] += 1
    node_common[t][s] += 1

# Update each node with top 3 common connections
count = 0
for ct, connections in node_common.items():
    top3 = [k for k, v in connections.most_common(3)]
    db_c.execute("UPDATE nodes SET common_connections = ? WHERE class_type = ?",
                 (json.dumps(top3), ct))
    count += 1

db_conn.commit()
print(f"Updated common_connections for {count} nodes")

# Verify
db_c.execute("SELECT COUNT(*) FROM nodes WHERE common_connections IS NOT NULL AND common_connections NOT IN ('[]', '')")
result = db_c.fetchone()[0]
print(f"Verified: {result} nodes have valid common_connections")

# Sample
db_c.execute("SELECT class_type, common_connections FROM nodes WHERE common_connections IS NOT NULL AND common_connections NOT IN ('[]', '') LIMIT 3")
for row in db_c.fetchall():
    print(f"  {row['class_type']}: {row['common_connections'][:60]}")

db_conn.close()
