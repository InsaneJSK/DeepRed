"""
autonomous_controller/path_cache.py

Goal-reachability cache for navigate_to_tile().

Design
------
Key:   "{map_name}:{goal_x},{goal_y}"   — NO start position in the key.
Value: {"runs": N, "locked": bool}

On every successful navigation, the goal is recorded as "reachable" on
that map.  On the next call with any starting position on the same map,
the cache generates a fresh OPTIMAL straight-line path from the player's
current position to the goal:

    optimal = up×|Δy| (if Δy<0)  +  right×|Δx| (if Δx>0)  etc.

This avoids two problems with storing direction sequences:
  1. Start-position misses — the key no longer varies with start position.
  2. Stored loops — the optimal path is always the shortest possible.

If the straight-line path fails (obstacle in the way), the caller falls
back to A* and the goal remains cached for the next attempt.

After LOCK_AFTER_RUNS confirmed successes the entry is locked and the
goal is considered permanently reachable on that map.
"""

from __future__ import annotations

import json
import os


class PathCache:
    LOCK_AFTER_RUNS: int = 3

    def __init__(self, cache_file: str = "path_cache.json") -> None:
        self._file = cache_file
        self._data: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_optimal_path(
        self,
        map_name: str,
        cx: int, cy: int,
        gx: int, gy: int,
    ) -> list[str] | None:
        """
        If this goal is known reachable on map_name, return a fresh
        optimal straight-line direction list from (cx,cy) to (gx,gy).
        Returns None if the goal has never been reached.
        """
        if not self._data.get(self._key(map_name, gx, gy)):
            return None
        return self._straight_line(cx, cy, gx, gy)

    def mark_reached(self, map_name: str, gx: int, gy: int) -> None:
        """Record that (gx, gy) was successfully navigated to."""
        key = self._key(map_name, gx, gy)
        entry = self._data.get(key, {"runs": 0, "locked": False})
        if entry.get("locked"):
            return
        entry["runs"] = entry.get("runs", 0) + 1
        if entry["runs"] >= self.LOCK_AFTER_RUNS:
            entry["locked"] = True
            print(f"  [CACHE] Goal {key!r} locked after {entry['runs']} runs.")
        self._data[key] = entry
        self._save()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _key(map_name: str, gx: int, gy: int) -> str:
        return f"{map_name}:{gx},{gy}"

    @staticmethod
    def _straight_line(cx: int, cy: int, gx: int, gy: int) -> list[str]:
        """Optimal path: vertical steps first, then horizontal."""
        dirs: list[str] = []
        dy, dx = gy - cy, gx - cx
        dirs += ["up"]    * max(0, -dy)
        dirs += ["down"]  * max(0,  dy)
        dirs += ["left"]  * max(0, -dx)
        dirs += ["right"] * max(0,  dx)
        return dirs

    def _load(self) -> None:
        if os.path.exists(self._file):
            try:
                with open(self._file, encoding="utf-8") as f:
                    raw = json.load(f)
                # Migrate old format (keys had start position "map:sx,sy→gx,gy")
                cleaned: dict[str, dict] = {}
                for k, v in raw.items():
                    # New format: "map:gx,gy"  (no → in key)
                    if "\u2192" in k:
                        # Old format — drop it; will be re-learned
                        continue
                    cleaned[k] = {"runs": v.get("runs", 0),
                                  "locked": v.get("locked", False)}
                self._data = cleaned
                print(f"[CACHE] Loaded {len(self._data)} goal(s) from {self._file}")
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[CACHE] Could not load {self._file}: {exc} — starting fresh.")
                self._data = {}

    def _save(self) -> None:
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as exc:
            print(f"[CACHE] Could not save {self._file}: {exc}")
