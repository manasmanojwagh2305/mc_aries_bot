"""
world_map.py — Aries Cartographer: Spatial Memory & World Mapping (Voyager Pillar 2)

Architecture:
  - In-memory only (no disk persistence at this milestone).
  - Organized by 16-block chunk keys for O(1) area lookups.
  - Secondary index by block type for O(1) type queries.
  - Provides a natural-language summary for injection into LLM Curriculum prompts.
"""

import math
import time
from typing import Optional, Dict, List, Any, Set

# ─── Block types worth tracking ──────────────────────────────────────────────
LANDMARK_BLOCKS: Set[str] = {
    # Ores
    "iron_ore", "gold_ore", "diamond_ore", "coal_ore", "lapis_ore",
    "emerald_ore", "redstone_ore", "copper_ore",
    "deepslate_iron_ore", "deepslate_gold_ore", "deepslate_diamond_ore",
    "deepslate_coal_ore", "deepslate_lapis_ore", "deepslate_emerald_ore",
    "deepslate_redstone_ore", "deepslate_copper_ore",
    # Nether / End
    "ancient_debris", "nether_gold_ore", "nether_quartz_ore",
    "end_portal_frame",
    # Structures / utility
    "chest", "trapped_chest", "ender_chest", "spawner",
    "nether_portal",
    # Environment
    "lava", "water",
    # Player-placed (tracked to remember own infrastructure)
    "crafting_table", "furnace", "blast_furnace",
    "bed", "respawn_anchor",
}

# Friendly display name map (used in LLM summary)
_FRIENDLY: Dict[str, str] = {
    "iron_ore":         "Iron ore",
    "gold_ore":         "Gold ore",
    "diamond_ore":      "Diamond ore",
    "coal_ore":         "Coal ore",
    "ancient_debris":   "Ancient debris (Netherite)",
    "chest":            "Chest",
    "spawner":          "Mob spawner",
    "lava":             "Lava pool",
    "crafting_table":   "Crafting table",
    "nether_portal":    "Nether portal",
    "end_portal_frame": "End portal frame",
}


class WorldMap:
    """
    Lightweight 3D spatial memory for the Aries Cartographer system.

    Data layout:
        _chunk_index: Dict["cx,cz" -> List[POI]]  — chunk-keyed primary index
        _type_index:  Dict[block_name -> List[POI]] — type-keyed secondary index

    POI record schema:
        {"type": str, "x": int, "y": int, "z": int, "ts": float}
    """

    def __init__(self):
        self._chunk_index: Dict[str, List[Dict[str, Any]]] = {}
        self._type_index:  Dict[str, List[Dict[str, Any]]] = {}
        self._total_logged: int = 0
        # Dedup cache: "type,x,y,z" → True
        self._seen: Dict[str, bool] = {}

    # ─── Chunk key ───────────────────────────────────────────────────────────

    @staticmethod
    def _chunk_key(x: float, z: float) -> str:
        return f"{int(x) >> 4},{int(z) >> 4}"

    # ─── Write ───────────────────────────────────────────────────────────────

    def log_poi(
        self,
        block_name: str,
        x: float,
        y: float,
        z: float,
        timestamp: Optional[float] = None,
    ) -> bool:
        """
        Record a discovered landmark block.
        Returns True if the record was new, False if it was a duplicate.
        """
        if block_name not in LANDMARK_BLOCKS:
            return False

        rx, ry, rz = round(x), round(y), round(z)
        dedup_key = f"{block_name},{rx},{ry},{rz}"
        if dedup_key in self._seen:
            return False

        ts = timestamp or time.time()
        poi: Dict[str, Any] = {"type": block_name, "x": rx, "y": ry, "z": rz, "ts": ts}

        ck = self._chunk_key(rx, rz)
        self._chunk_index.setdefault(ck, []).append(poi)
        self._type_index.setdefault(block_name, []).append(poi)
        self._seen[dedup_key] = True
        self._total_logged += 1
        return True

    def log_batch(self, blocks: List[Dict[str, Any]], timestamp: Optional[float] = None) -> int:
        """Bulk-log a list of {'name', 'x', 'y', 'z'} dicts. Returns count of new records."""
        count = 0
        ts = timestamp or time.time()
        for b in blocks:
            try:
                if self.log_poi(b["name"], b["x"], b["y"], b["z"], ts):
                    count += 1
            except (KeyError, TypeError):
                pass
        return count

    # ─── Read ─────────────────────────────────────────────────────────────────

    def query_nearest(
        self,
        poi_type: str,
        from_pos: Dict[str, float],
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Return the closest POIs of a given type to from_pos,
        sorted by 3D Euclidean distance (ascending).
        """
        fx = from_pos.get("x", 0.0)
        fy = from_pos.get("y", 64.0)
        fz = from_pos.get("z", 0.0)

        def dist3d(p: Dict[str, Any]) -> float:
            return math.sqrt(
                (p["x"] - fx) ** 2 +
                (p["y"] - fy) ** 2 +
                (p["z"] - fz) ** 2
            )

        candidates = self._type_index.get(poi_type, [])
        if not candidates:
            return []
        return sorted(candidates, key=dist3d)[:max_results]

    def query_area(
        self,
        x: float,
        z: float,
        radius_chunks: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Return all POIs within radius_chunks chunks of (x, z).
        Useful for 'what is near me?' context.
        """
        base_cx = int(x) >> 4
        base_cz = int(z) >> 4
        results: List[Dict[str, Any]] = []
        for dcx in range(-radius_chunks, radius_chunks + 1):
            for dcz in range(-radius_chunks, radius_chunks + 1):
                ck = f"{base_cx + dcx},{base_cz + dcz}"
                results.extend(self._chunk_index.get(ck, []))
        return results

    def query_type_count(self) -> Dict[str, int]:
        """Return count of discovered POIs per type."""
        return {k: len(v) for k, v in self._type_index.items() if v}

    # ─── LLM context ──────────────────────────────────────────────────────────

    def summarize_for_llm(
        self,
        from_pos: Optional[Dict[str, float]] = None,
        max_entries: int = 20,
    ) -> str:
        """
        Generate a compact natural-language paragraph for injection into
        the Curriculum Planner LLM prompt.
        Sorted by count descending. Includes coords of 2 nearest examples.
        """
        if self._total_logged == 0:
            return (
                "World map is empty — the bot has not moved far enough to "
                "discover any landmarks yet. Encourage exploration."
            )

        lines: List[str] = [
            f"Cartographer has logged {self._total_logged} spatial landmarks "
            f"across {len(self._chunk_index)} explored chunks."
        ]

        # Sort by frequency (most-seen types first)
        sorted_types = sorted(
            self._type_index.items(), key=lambda kv: -len(kv[1])
        )

        for block_name, records in sorted_types[:max_entries]:
            count = len(records)
            friendly = _FRIENDLY.get(block_name, block_name.replace("_", " ").title())

            if from_pos and records:
                # Show nearest 2 to current position
                near = self.query_nearest(block_name, from_pos, max_results=2)
                coord_strs = [f"({r['x']},{r['y']},{r['z']})" for r in near]
            else:
                coord_strs = [f"({r['x']},{r['y']},{r['z']})" for r in records[:2]]

            lines.append(
                f"  • {friendly}: {count} found. Nearest: {', '.join(coord_strs)}"
            )

        return "\n".join(lines)

    # ─── Serialization ────────────────────────────────────────────────────────

    def get_all(self) -> Dict[str, Any]:
        """Return full map state for the /agent/map/all endpoint."""
        return {
            "total_logged":    self._total_logged,
            "explored_chunks": len(self._chunk_index),
            "type_counts":     self.query_type_count(),
            "by_type":         dict(self._type_index),
        }

    def clear(self):
        """Reset all spatial memory (for testing or new session)."""
        self._chunk_index.clear()
        self._type_index.clear()
        self._seen.clear()
        self._total_logged = 0


# ─── Module-level singleton ───────────────────────────────────────────────────
world_map = WorldMap()
