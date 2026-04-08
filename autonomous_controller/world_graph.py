"""
autonomous_controller/world_graph.py

WorldGraph: loads world_graph.json and provides BFS routing,
warp/connection lookups.
"""

import json
from collections import deque

class WorldGraph:
    """
    Loads world_graph.json and provides BFS routing, warp/connection lookups.
    """
    def __init__(self, graph_path: str):
        with open(graph_path, encoding="utf-8") as f:
            data = json.load(f)
        self.maps: dict = data["maps"]
        self.name_to_id: dict[str, int] = data["map_name_to_id"]
        self.id_to_name: dict[int, str] = {
            int(k): v for k, v in data["map_id_to_name"].items()
        }

    def map_name(self, map_id: int) -> str | None:
        """Returns map name for given map ID, or None if not found."""
        return self.id_to_name.get(map_id)

    def map_id(self, map_name: str) -> int | None:
        """Returns map ID for given map name, or None if not found."""
        return self.name_to_id.get(map_name.upper())

    def warps(self, map_name: str) -> list[dict]:
        """Returns list of warps on given map, or empty list if map not found."""
        return self.maps.get(map_name.upper(), {}).get("warps", [])

    def connections(self, map_name: str) -> dict:
        """Returns dictionary of connections for given map, or empty dict if map not found."""
        return self.maps.get(map_name.upper(), {}).get("connections", {})

    def neighbors(self, map_name: str) -> list[str]:
        """Returns list of neighboring map names (via warps or connections)."""
        result = []
        for warp in self.warps(map_name):
            result.append(warp["dest_map"])
        for conn in self.connections(map_name).values():
            result.append(conn["map"])
        return result

    def bfs_route(self, src: str, dst: str) -> list[str] | None:
        """BFS over map graph. Returns map name sequence src→dst inclusive."""
        src, dst = src.upper(), dst.upper()
        if src == dst:
            return [src]
        visited = {src}
        queue = deque([[src]])
        while queue:
            path = queue.popleft()
            current = path[-1]
            for neighbor in self.neighbors(current):
                if neighbor not in visited:
                    new_path = path + [neighbor]
                    if neighbor == dst:
                        return new_path
                    visited.add(neighbor)
                    queue.append(new_path)
        return None
