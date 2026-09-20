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
        if data.get("schema_version") != 2:
            raise ValueError("World graph is outdated; regenerate it with build_world_graph.py")
        self.maps: dict = data["maps"]
        self.name_to_id: dict[str, int] = data["map_name_to_id"]
        self.id_to_name: dict[int, str] = {int(k): v for k, v in data["map_id_to_name"].items()}

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
            result.extend(warp.get("dest_map_candidates", [warp["dest_map"]]))
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

    def terrain_route(self, src, dst, position, terrain, last_map=None):
        """BFS over map *regions*, preventing routes through inaccessible entrances."""
        from autonomous_controller.constants import COMPASS_TO_ARROW

        region = terrain.components(src).get(position)
        if region is None:
            return None
        start = (src, region)
        queue, visited = deque([(start, [src])]), {start}
        while queue:
            (name, region), path = queue.popleft()
            if name == dst:
                return path
            edges = []
            for warp in self.warps(name):
                if terrain.components(name).get((warp["x"], warp["y"])) != region:
                    continue
                index = warp["dest_warp_index"] - 1
                destinations = warp.get("dest_map_candidates", [warp["dest_map"]])
                if not destinations and name == src and last_map:
                    destinations = [last_map]
                for dest in destinations:
                    warps = self.warps(dest)
                    if 0 <= index < len(warps):
                        landing = (warps[index]["x"], warps[index]["y"])
                        edges.append((dest, terrain.components(dest).get(landing)))
            for compass, conn in self.connections(name).items():
                dest = conn["map"]
                for border, landing in terrain.connection_tiles(
                    name, dest, COMPASS_TO_ARROW[compass], conn.get("offset", 0)
                ):
                    if terrain.components(name).get(border) == region:
                        edges.append((dest, terrain.components(dest).get(landing)))
            for edge in edges:
                if edge[1] is not None and edge not in visited:
                    visited.add(edge)
                    queue.append((edge, path + [edge[0]]))
        return None
