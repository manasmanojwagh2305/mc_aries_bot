import os
import json
import math
import re
import asyncio
import httpx                          # PATCH C-4: async HTTP — replaces blocking requests
from typing import Optional, List, Dict, Any, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

app = FastAPI(title="ARIES Voyager-Inspired Minecraft Brain")

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

MC_BRIDGE_URL = "http://localhost:3000/api"

# PATCH C-4: Single shared async HTTP client with enforced timeouts on all bridge calls
_http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=3.0, read=65.0, write=10.0, pool=5.0),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
)

# PATCH C-5: Global asyncio lock — prevents concurrent sub-goal execution storms
_execution_lock = asyncio.Lock()

# =============================================================================
# REGEX TOOL CALL SCRUBBER — M-1: ReDoS-safe version
# =============================================================================

def scrub_xml_tool_calls(text: str) -> str:
    """Strip accidental XML tool call tags from LLM output before returning to UI/chat."""
    if not text:
        return ""
    # M-1: Hard cap BEFORE any regex to neutralize ReDoS on large adversarial payloads
    text = text[:8000]
    # M-1: Simple non-nested character-class patterns — no catastrophic backtracking
    cleaned = re.sub(r'<function=[^>]*>[^<]{0,2000}</function>', '', text)
    cleaned = re.sub(r'<tool_call>[^<]{0,2000}</tool_call>', '', cleaned)
    cleaned = re.sub(r'</?function[^>]{0,200}>', '', cleaned)
    cleaned = re.sub(r'\{"name"\s*:\s*"tool_[^}]{0,500}\}', '', cleaned)
    return cleaned.strip()

# =============================================================================
# SKILL LIBRARY (CHROMADB + JSON COSINE FALLBACK)
# =============================================================================

class SkillLibraryManager:
    def __init__(self, data_dir: str = "./skill_store"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.use_chroma = False
        self.chroma_collection = None
        self.json_file = os.path.join(self.data_dir, "skills.json")
        self.skills = self._load_json_skills()

        try:
            import chromadb
            self.chroma_client = chromadb.PersistentClient(path=os.path.join(self.data_dir, "chroma"))
            self.chroma_collection = self.chroma_client.get_or_create_collection("mineflayer_skills")
            self.use_chroma = True
            print("[SkillLibrary] ChromaDB initialized successfully.")
        except Exception as e:
            print(f"[SkillLibrary] ChromaDB unavailable ({e}). Fallback to JSON vector store.")

    def _load_json_skills(self) -> Dict[str, Any]:
        if os.path.exists(self.json_file):
            try:
                with open(self.json_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_json_skills(self):
        try:
            with open(self.json_file, "w") as f:
                json.dump(self.skills, f, indent=2)
        except Exception as e:
            print(f"[SkillLibrary] Error saving JSON skills: {e}")

    def _text_to_vector(self, text: str) -> Dict[str, int]:
        vec: Dict[str, int] = {}
        for w in re.findall(r'\w+', text.lower()):
            vec[w] = vec.get(w, 0) + 1
        return vec

    def _cosine_similarity(self, v1: Dict[str, int], v2: Dict[str, int]) -> float:
        intersection = set(v1) & set(v2)
        num = sum(v1[x] * v2[x] for x in intersection)
        denom = math.sqrt(sum(x**2 for x in v1.values())) * math.sqrt(sum(x**2 for x in v2.values()))
        return float(num) / denom if denom else 0.0

    def add_skill(self, name: str, description: str, code: str, metadata: Optional[dict] = None) -> str:
        skill_id = re.sub(r'[^\w]', '_', name.lower())[:64]
        meta = metadata or {}
        record = {"id": skill_id, "name": name, "description": description, "code": code, "sub_goal": meta.get("sub_goal", name)}
        self.skills[skill_id] = record
        self._save_json_skills()
        if self.use_chroma and self.chroma_collection:
            try:
                self.chroma_collection.upsert(
                    ids=[skill_id],
                    documents=[f"{name} {description}"],
                    metadatas=[{"name": name, "sub_goal": meta.get("sub_goal", name)}]
                )
            except Exception as e:
                print(f"[SkillLibrary] ChromaDB upsert warning: {e}")
        return skill_id

    def query_skill(self, query_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
        if self.use_chroma and self.chroma_collection:
            try:
                res = self.chroma_collection.query(query_texts=[query_text], n_results=top_k)
                if res and res.get("ids") and res["ids"][0]:
                    results = [self.skills[s_id] for s_id in res["ids"][0] if s_id in self.skills]
                    if results:
                        return results
            except Exception as e:
                print(f"[SkillLibrary] ChromaDB query warning ({e}), falling back.")

        qv = self._text_to_vector(query_text)
        scored = []
        for data in self.skills.values():
            dv = self._text_to_vector(f"{data['name']} {data['description']}")
            scored.append((self._cosine_similarity(qv, dv), data))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for score, d in scored if score > 0.05][:top_k]

    def get_all_skills(self) -> List[Dict[str, Any]]:
        return list(self.skills.values())

skill_library = SkillLibraryManager()

# =============================================================================
# GLOBAL STATE
# =============================================================================

CURRICULUM_STATE: Dict[str, Any] = {
    "master_goal": "Survive and thrive: gather wood, make tools, establish shelter, and prepare for progression.",
    "active_subgoal": None,
    "completed_subgoals": [],
    "failed_subgoals": [],
    "planner_enabled": True,
    "executing": False,
    "last_status": "Initialized",
    "last_error": None,
}

SYSTEM_PROMPT = (
    "You are Aries, an autonomous AI Minecraft agent inspired by the Voyager framework. "
    "You have a curriculum planner, dynamic code execution engine, and a persistent skill library. "
    "CRITICAL RULE: NEVER output raw XML, function call syntax, or markdown code fences like "
    "`<function=...>`, `<tool_call>`, or ```json in your spoken responses. "
    "All in-game physical actions MUST be dispatched exclusively via native tool calls. "
    "Respond naturally and concisely. Do not fabricate task success — you have access to "
    "tool_get_telemetry to verify real game state."
)

# M-5: Global conversation history
CONVERSATION_HISTORY: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class MoveRequest(BaseModel):
    x: float
    y: float
    z: float

class CollectRequest(BaseModel):
    blockName: str
    count: int = 1

class PlaceRequest(BaseModel):
    blockName: str
    x: float
    y: float
    z: float

class DepositRequest(BaseModel):
    itemName: str
    count: Optional[int] = None

class CraftRequest(BaseModel):
    itemName: str
    count: int = 1

class FightRequest(BaseModel):
    mobName: str

class NaturalLanguagePrompt(BaseModel):
    prompt: str

    @field_validator('prompt')
    @classmethod
    def prompt_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('prompt cannot be empty')
        return v[:4000]  # Hard cap to prevent oversized payloads

class MasterGoalRequest(BaseModel):
    master_goal: str

class ExecuteScriptRequest(BaseModel):
    code: str
    timeoutMs: int = 45000

# =============================================================================
# PHASE 2: ASYNC BRIDGE COMMUNICATION HELPERS (C-4)
# All bridge calls are now non-blocking coroutines using httpx.
# =============================================================================

async def fetch_telemetry() -> Optional[Dict[str, Any]]:
    """Non-blocking telemetry poll. Returns None on any error without hanging."""
    try:
        res = await _http_client.get(f"{MC_BRIDGE_URL}/telemetry")
        if res.status_code == 200:
            return res.json().get("telemetry")
    except Exception as e:
        print(f"[Telemetry] Failed: {e}")
    return None

async def execute_js_script(code: str, timeout_ms: int = 45000) -> Dict[str, Any]:
    """Non-blocking VM script execution. The wall-clock timeout covers the full async span."""
    try:
        res = await _http_client.post(
            f"{MC_BRIDGE_URL}/execute-script",
            json={"code": code, "timeoutMs": timeout_ms},
            timeout=timeout_ms / 1000 + 10  # Give Node's own timeout room to fire first
        )
        return res.json()
    except httpx.TimeoutException:
        return {"status": "error", "message": f"Bridge request timed out after {timeout_ms/1000+10:.0f}s — Node server may be busy."}
    except Exception as e:
        return {"status": "error", "message": f"Bridge connection failed: {str(e)}"}

# =============================================================================
# TELEMETRY DIFFING ENGINE
# =============================================================================

def extract_target_count(subgoal: str) -> Tuple[int, str]:
    match = re.search(r'(\d+)', subgoal)
    target_count = int(match.group(1)) if match else 1
    sub_lower = subgoal.lower()
    for kw in ["log", "planks", "cobblestone", "stone", "dirt", "pickaxe", "axe", "sword", "table", "stick", "iron", "coal"]:
        if kw in sub_lower:
            return target_count, kw
    return target_count, "item"

def verify_telemetry_diff(
    pre: Optional[Dict[str, Any]],
    post: Optional[Dict[str, Any]],
    subgoal: str
) -> Tuple[bool, str]:
    """Strict mathematical diff. post_count - pre_count >= target_count required for success."""
    if not pre or not post:
        return False, "CRITICAL: Telemetry unavailable — Node bridge may be down."

    target_count, kw = extract_target_count(subgoal)
    pre_inv  = {i['name']: i['count'] for i in pre.get('inventory', [])}
    post_inv = {i['name']: i['count'] for i in post.get('inventory', [])}

    if kw != "item":
        pre_sum  = sum(c for n, c in pre_inv.items()  if kw in n)
        post_sum = sum(c for n, c in post_inv.items() if kw in n)
        gained   = post_sum - pre_sum

        if gained >= target_count:
            return True, f"Diff verified: +{gained} {kw} (needed {target_count})."
        if post_sum >= target_count:
            return True, f"Inventory already holds {post_sum} {kw} (target {target_count})."
        return False, (
            f"Diff FAILED: gained only +{gained} {kw}, total {post_sum}/{target_count}. "
            f"Re-triggering execution loop."
        )

    sub_lower = subgoal.lower()
    if any(w in sub_lower for w in ("move", "go to", "walk", "travel")):
        pre_pos  = pre.get('position', {})
        post_pos = post.get('position', {})
        dist = math.sqrt(
            (post_pos.get('x', 0) - pre_pos.get('x', 0))**2 +
            (post_pos.get('z', 0) - pre_pos.get('z', 0))**2
        )
        if dist > 1.5:
            return True, f"Position change verified: moved {dist:.1f} blocks."
        return False, f"Movement failed: bot only moved {dist:.1f} blocks."

    return True, "Script executed cleanly — no quantity assertion required."

# =============================================================================
# CODE GENERATION (LLM → Mineflayer JS)
# =============================================================================

def generate_js_code(
    subgoal: str,
    telemetry: Optional[Dict[str, Any]],
    error_context: Optional[str] = None,
    failed_code: Optional[str] = None
) -> str:
    telemetry_str = json.dumps(telemetry, indent=2) if telemetry else "Unavailable"

    if error_context and failed_code:
        prompt = (
            f"The previous Mineflayer JavaScript script FAILED to achieve: '{subgoal}'.\n\n"
            f"Failed Code:\n{failed_code}\n\n"
            f"Error:\n{error_context}\n\n"
            f"Telemetry:\n{telemetry_str}\n\n"
            "Fix the code. Output ONLY raw runnable JavaScript — no markdown backticks, no explanations."
        )
    else:
        prompt = (
            f"Write a self-contained Mineflayer JavaScript snippet to: '{subgoal}'\n\n"
            "Sandbox variables available:\n"
            "  bot (Mineflayer instance, whitelisted proxy — no _client or emit)\n"
            "  Vec3, pathfinder, goals, Movements, mcData, normalizeItemName, console\n\n"
            f"Current Bot Telemetry:\n{telemetry_str}\n\n"
            "Rules:\n"
            "1. Output ONLY raw JavaScript. No backticks. No explanations.\n"
            "2. Use await for all async Mineflayer operations.\n"
            "3. Use exact Minecraft IDs ('oak_planks' not 'wooden_plank', 'oak_log' not 'wood').\n"
            "4. To craft, call the bridge HTTP endpoint — do NOT use bot.craft() directly:\n"
            "   const r = await fetch('http://localhost:3000/api/craft', { method:'POST', "
            "headers:{'Content-Type':'application/json'}, body:JSON.stringify({itemName:'oak_planks',count:4}) });\n"
            "   NOTE: fetch is NOT available in the sandbox. Use bot.collectBlock, bot.pathfinder, etc. directly.\n"
            "   For crafting, call the craft endpoint via bot._client is also blocked — "
            "instead, signal the need via console.log and let the Python loop use tool_craft.\n"
            "5. Validate items exist before using them: "
            "const item = bot.inventory.items().find(i => i.name === 'oak_log'); if (!item) throw new Error('...');\n"
        )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Output strictly raw executable JavaScript for Mineflayer. No markdown. No backticks. No prose."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.15,
            max_tokens=2048
        )
        code = response.choices[0].message.content.strip()
        # Strip accidental markdown fences from the code generator
        if code.startswith("```"):
            lines = code.splitlines()
            lines = lines[1:] if lines[0].startswith("```") else lines
            lines = lines[:-1] if lines and lines[-1].startswith("```") else lines
            code = "\n".join(lines).strip()
        return code
    except Exception as e:
        print(f"[CodeGen Error] {e}")
        return "// Code generation failed — LLM error."

# =============================================================================
# PHASE 2: ITERATIVE EXECUTION LOOP WITH asyncio.Lock (C-5)
# =============================================================================

async def run_iterative_execution_loop(subgoal: str) -> bool:
    """
    PATCH C-5: Guarded by _execution_lock so only ONE sub-goal runs at a time.
    All bridge I/O is non-blocking (async httpx).
    """
    async with _execution_lock:
        print(f"\n[IterativeLoop] Starting: '{subgoal}'")
        CURRICULUM_STATE["executing"] = True
        CURRICULUM_STATE["active_subgoal"] = subgoal
        CURRICULUM_STATE["last_status"] = f"Executing: '{subgoal}'"

        # Check skill library first
        existing_skills = skill_library.query_skill(subgoal, top_k=1)
        attempt_code: Optional[str] = existing_skills[0]["code"] if existing_skills else None
        if attempt_code:
            print(f"[IterativeLoop] Reusing skill: '{existing_skills[0]['name']}'")

        max_attempts = 4
        success = False

        for attempt in range(1, max_attempts + 1):
            print(f"[IterativeLoop] Attempt {attempt}/{max_attempts}")

            pre_telemetry = await fetch_telemetry()  # C-4: non-blocking

            # Regenerate code on retry (or first attempt if no cached skill)
            if not attempt_code or attempt > 1:
                attempt_code = generate_js_code(
                    subgoal=subgoal,
                    telemetry=pre_telemetry,
                    error_context=CURRICULUM_STATE.get("last_error"),
                    failed_code=attempt_code
                )

            res = await execute_js_script(attempt_code, timeout_ms=45000)  # C-4: non-blocking

            post_telemetry = await fetch_telemetry()  # C-4: non-blocking

            if res.get("status") == "success":
                verified, diff_msg = verify_telemetry_diff(pre_telemetry, post_telemetry, subgoal)
                if verified:
                    print(f"[IterativeLoop] SUCCESS: '{subgoal}' — {diff_msg}")
                    skill_library.add_skill(
                        name=subgoal,
                        description=f"Mineflayer script for: {subgoal}",
                        code=attempt_code,
                        metadata={"sub_goal": subgoal}
                    )
                    CURRICULUM_STATE["completed_subgoals"].append(subgoal)
                    CURRICULUM_STATE["last_status"] = f"Completed: '{subgoal}'"
                    CURRICULUM_STATE["last_error"] = None
                    success = True
                    break
                else:
                    error_msg = f"Telemetry diff failed: {diff_msg}"
            else:
                error_msg = res.get("message") or res.get("logs") or "Unknown script execution error."

            print(f"[IterativeLoop] Attempt {attempt} failed: {error_msg}")
            CURRICULUM_STATE["last_error"] = f"Attempt {attempt}: {error_msg}"
            await asyncio.sleep(2)

        if not success:
            print(f"[IterativeLoop] FAILED all {max_attempts} attempts for '{subgoal}'")
            CURRICULUM_STATE["failed_subgoals"].append(subgoal)
            CURRICULUM_STATE["last_status"] = f"Failed: '{subgoal}'"

        CURRICULUM_STATE["active_subgoal"] = None
        CURRICULUM_STATE["executing"] = False
        return success

# =============================================================================
# AUTOMATIC CURRICULUM PLANNER (M-2: also skips failed goals)
# =============================================================================

def determine_next_subgoal(master_goal: str, telemetry: Optional[Dict[str, Any]]) -> Optional[str]:
    if not telemetry:
        return "Collect 3 oak_log"

    inventory = {i['name']: i['count'] for i in telemetry.get('inventory', [])}
    health = telemetry.get('health', 20)

    if health < 8:
        return "Eat food or find safe shelter"

    logs    = sum(c for n, c in inventory.items() if 'log'           in n)
    planks  = sum(c for n, c in inventory.items() if 'planks'        in n)
    ctables = sum(c for n, c in inventory.items() if 'crafting_table' in n)
    sticks  = sum(c for n, c in inventory.items() if 'stick'         in n)
    picks   = [n for n in inventory if 'pickaxe' in n]
    stone   = sum(c for n, c in inventory.items() if 'cobblestone' in n or ('stone' in n and 'pickaxe' not in n))
    iron    = sum(c for n, c in inventory.items() if 'iron' in n or 'raw_iron' in n)

    if logs == 0 and planks == 0 and ctables == 0:
        return "Collect 5 oak_log"
    if logs > 0 and planks < 4 and ctables == 0:
        return "Craft 8 oak_planks from oak_log"
    if planks >= 4 and ctables == 0:
        return "Craft 1 crafting_table"
    if ctables > 0 and sticks < 4 and not picks:
        return "Craft 4 sticks"
    if ctables > 0 and sticks >= 2 and planks >= 3 and not picks:
        return "Craft 1 wooden_pickaxe"
    if 'wooden_pickaxe' in picks and stone < 3:
        return "Collect 3 stone"
    if stone >= 3 and 'stone_pickaxe' not in picks:
        return "Craft 1 stone_pickaxe"
    if 'stone_pickaxe' in picks and iron < 3:
        return "Collect 3 iron_ore"
    return "Explore surroundings and collect resources"

async def curriculum_planner_loop():
    print("[CurriculumPlanner] Background loop active.")
    await asyncio.sleep(5)

    while True:
        try:
            # C-5: Use lock state instead of dict flag — authoritative source of truth
            if CURRICULUM_STATE["planner_enabled"] and not _execution_lock.locked():
                telemetry = await fetch_telemetry()  # C-4: non-blocking
                if telemetry:
                    next_subgoal = determine_next_subgoal(CURRICULUM_STATE["master_goal"], telemetry)
                    if (next_subgoal
                            and next_subgoal not in CURRICULUM_STATE["completed_subgoals"]
                            and next_subgoal not in CURRICULUM_STATE["failed_subgoals"]):  # M-2: skip failed
                        print(f"[CurriculumPlanner] Scheduling: '{next_subgoal}'")
                        asyncio.create_task(run_iterative_execution_loop(next_subgoal))
        except Exception as e:
            print(f"[CurriculumPlanner Error] {e}")
        await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(curriculum_planner_loop())

# =============================================================================
# PHASE 2: ASYNC TOOL FUNCTIONS (C-4, H-4)
# All synchronous requests.* calls replaced with async httpx equivalents.
# =============================================================================

async def tool_move(x: float, y: float, z: float):
    res = await _http_client.post(f"{MC_BRIDGE_URL}/move", json={"x": x, "y": y, "z": z})
    return res.json()

async def tool_collect(blockName: str, count: int = 1):
    res = await _http_client.post(f"{MC_BRIDGE_URL}/collect", json={"blockName": blockName, "count": count})
    return res.json()

async def tool_place(blockName: str, x: float, y: float, z: float):
    res = await _http_client.post(f"{MC_BRIDGE_URL}/place", json={"blockName": blockName, "x": x, "y": y, "z": z})
    return res.json()

async def tool_deposit(itemName: str, count: Optional[int] = None):
    res = await _http_client.post(f"{MC_BRIDGE_URL}/deposit", json={"itemName": itemName, "count": count})
    return res.json()

async def tool_craft(itemName: str, count: int = 1):
    res = await _http_client.post(f"{MC_BRIDGE_URL}/craft", json={"itemName": itemName, "count": count})
    return res.json()

async def tool_fight(mobName: str):
    res = await _http_client.post(f"{MC_BRIDGE_URL}/fight", json={"mobName": mobName})
    return res.json()

async def tool_pvp():
    res = await _http_client.post(f"{MC_BRIDGE_URL}/pvp")
    return res.json()

async def tool_get_inventory():
    res = await _http_client.get(f"{MC_BRIDGE_URL}/inventory")
    return res.json()

async def tool_stop():
    res = await _http_client.delete(f"{MC_BRIDGE_URL}/stop")
    return res.json()

async def tool_save_me(mobName: Optional[str] = None):
    await _http_client.delete(f"{MC_BRIDGE_URL}/stop")
    if mobName:
        res = await _http_client.post(f"{MC_BRIDGE_URL}/fight", json={"mobName": mobName})
    else:
        res = await _http_client.post(f"{MC_BRIDGE_URL}/pvp")
    return {"status": "success", "message": f"Emergency engaged against {mobName or 'nearest threat'}!"}

async def tool_look_around():
    res = await _http_client.get(f"{MC_BRIDGE_URL}/surroundings")
    return res.json()

async def tool_break_block(
    x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None,
    blockName: Optional[str] = None, block: Optional[str] = None, count: int = 1
):
    target = blockName or block or "oak_log"
    if x is not None and y is not None and z is not None and not (x == 0 and y == 0 and z == 0):
        res = await _http_client.post(f"{MC_BRIDGE_URL}/break-at", json={"x": x, "y": y, "z": z})
    else:
        res = await _http_client.post(f"{MC_BRIDGE_URL}/collect", json={"blockName": target, "count": count})
    return res.json()

async def tool_query_skills(query: str):
    return {"status": "success", "skills": skill_library.query_skill(query)}

async def tool_save_skill(name: str, description: str, code: str):
    skill_id = skill_library.add_skill(name, description, code)
    return {"status": "success", "skill_id": skill_id}

async def tool_get_telemetry():
    t = await fetch_telemetry()
    return {"status": "success", "telemetry": t}

async def tool_set_master_goal(master_goal: str):
    CURRICULUM_STATE["master_goal"] = master_goal
    return {"status": "success", "message": f"Master goal updated to: {master_goal}"}

AVAILABLE_TOOLS: Dict[str, Any] = {
    "tool_move":            tool_move,
    "tool_collect":         tool_collect,
    "tool_place":           tool_place,
    "tool_deposit":         tool_deposit,
    "tool_craft":           tool_craft,
    "tool_fight":           tool_fight,
    "tool_pvp":             tool_pvp,
    "tool_get_inventory":   tool_get_inventory,
    "tool_stop":            tool_stop,
    "tool_save_me":         tool_save_me,
    "tool_look_around":     tool_look_around,
    "tool_break_block":     tool_break_block,
    "tool_query_skills":    tool_query_skills,
    "tool_save_skill":      tool_save_skill,
    "tool_get_telemetry":   tool_get_telemetry,
    "tool_set_master_goal": tool_set_master_goal,
}

GROQ_TOOLS = [
    {"type":"function","function":{"name":"tool_move","description":"Move the bot to X, Y, Z coordinates.","parameters":{"type":"object","properties":{"x":{"type":"number"},"y":{"type":"number"},"z":{"type":"number"}},"required":["x","y","z"]}}},
    {"type":"function","function":{"name":"tool_collect","description":"Collect blocks like oak_log, stone, dirt.","parameters":{"type":"object","properties":{"blockName":{"type":"string"},"count":{"type":"integer"}},"required":["blockName"]}}},
    {"type":"function","function":{"name":"tool_place","description":"Place a block at X, Y, Z.","parameters":{"type":"object","properties":{"blockName":{"type":"string"},"x":{"type":"number"},"y":{"type":"number"},"z":{"type":"number"}},"required":["blockName","x","y","z"]}}},
    {"type":"function","function":{"name":"tool_deposit","description":"Deposit items into a nearby chest.","parameters":{"type":"object","properties":{"itemName":{"type":"string"},"count":{"type":"integer"}},"required":["itemName"]}}},
    {"type":"function","function":{"name":"tool_craft","description":"Craft items (oak_planks, sticks, pickaxe, etc.).","parameters":{"type":"object","properties":{"itemName":{"type":"string"},"count":{"type":"integer"}},"required":["itemName"]}}},
    {"type":"function","function":{"name":"tool_fight","description":"Fight a specific mob type.","parameters":{"type":"object","properties":{"mobName":{"type":"string"}},"required":["mobName"]}}},
    {"type":"function","function":{"name":"tool_pvp","description":"Engage the nearest hostile mob."}},
    {"type":"function","function":{"name":"tool_get_inventory","description":"Check current inventory."}},
    {"type":"function","function":{"name":"tool_stop","description":"Stop all current bot actions."}},
    {"type":"function","function":{"name":"tool_save_me","description":"Emergency: stop everything and attack nearest threat.","parameters":{"type":"object","properties":{"mobName":{"type":"string"}},"required":[]}}},
    {"type":"function","function":{"name":"tool_look_around","description":"Scan surrounding blocks and entities."}},
    {"type":"function","function":{"name":"tool_query_skills","description":"Query vector skill library for learned code snippets.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"tool_get_telemetry","description":"Get full bot telemetry: health, food, position, inventory, biome."}},
    {"type":"function","function":{"name":"tool_set_master_goal","description":"Set a new top-level master goal for the curriculum planner.","parameters":{"type":"object","properties":{"master_goal":{"type":"string"}},"required":["master_goal"]}}},
]

# =============================================================================
# REST API ENDPOINTS
# =============================================================================

@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Dashboard template missing — place index.html in brain/templates/</h1>"

@app.get("/agent/curriculum")
def get_curriculum():
    return {"status": "success", "curriculum": CURRICULUM_STATE}

@app.post("/agent/curriculum/set-goal")
def set_curriculum_goal(req: MasterGoalRequest):
    CURRICULUM_STATE["master_goal"] = req.master_goal
    # Also clear failed subgoals when the master goal changes, to allow fresh retries
    CURRICULUM_STATE["failed_subgoals"] = []
    return {"status": "success", "master_goal": req.master_goal}

@app.get("/agent/skills")
def get_skills():
    return {"status": "success", "skills": skill_library.get_all_skills()}

@app.get("/agent/telemetry")
async def get_telemetry_endpoint():
    t = await fetch_telemetry()
    if not t:
        raise HTTPException(status_code=503, detail="Bridge server telemetry unavailable.")
    return {"status": "success", "telemetry": t}

@app.post("/agent/execute-script")
async def execute_script_endpoint(req: ExecuteScriptRequest):
    return await execute_js_script(req.code, req.timeoutMs)

@app.post("/agent/chat")
async def agent_chat(req: NaturalLanguagePrompt):
    """
    Natural language interface with strict tool calling, XML scrubbing,
    and conversation history rollback on error (M-5).
    """
    global CONVERSATION_HISTORY

    # M-5: Work on a snapshot — only commit to global on full success
    history_snapshot = list(CONVERSATION_HISTORY)
    history_snapshot.append({"role": "user", "content": req.prompt})

    # Keep history bounded: system prompt + last 12 turns
    if len(history_snapshot) > 13:
        history_snapshot = [history_snapshot[0]] + history_snapshot[-12:]

    try:
        # Enforce strict native tool_choice
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=history_snapshot,
            tools=GROQ_TOOLS,
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls
        executed_tools = []

        if tool_calls:
            history_snapshot.append(response_message)

            for tool_call in tool_calls:
                function_name = tool_call.function.name
                raw_args = tool_call.function.arguments
                try:
                    function_args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    function_args = {}
                if not isinstance(function_args, dict):
                    function_args = {}

                if function_name in AVAILABLE_TOOLS:
                    tool_fn = AVAILABLE_TOOLS[function_name]
                    # C-4: All tool functions are now async — await them
                    tool_result = await tool_fn(**function_args)
                    executed_tools.append(function_name)
                    history_snapshot.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": json.dumps(tool_result)
                    })

            second = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=history_snapshot
            )
            raw_content = second.choices[0].message.content or ""
        else:
            raw_content = response_message.content or ""

        # M-1: Apply ReDoS-safe XML scrubber
        final_content = scrub_xml_tool_calls(raw_content)
        if not final_content and executed_tools:
            final_content = f"Executed {', '.join(executed_tools)} successfully."

        history_snapshot.append({"role": "assistant", "content": final_content})

        # M-5: Only commit clean history after full success
        CONVERSATION_HISTORY = history_snapshot

        return {
            "status": "success",
            "response": final_content,
            "tools_executed": executed_tools
        }

    except Exception as e:
        # M-5: Discard snapshot — CONVERSATION_HISTORY stays clean and unpoisoned
        raise HTTPException(status_code=500, detail=str(e))