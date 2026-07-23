import os
import json
import math
import re
import asyncio
import httpx
from typing import Optional, List, Dict, Any, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from groq import Groq

# Pillar 2: Cartographer spatial memory
from world_map import world_map

load_dotenv()

app = FastAPI(title="ARIES Voyager Cognitive Brain")

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

MC_BRIDGE_URL = "http://localhost:3000/api"

# Shared async HTTP client (C-4 hardening — non-blocking bridge calls)
_http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=3.0, read=65.0, write=10.0, pool=5.0),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)

# Pillar 1 + 4: Single asyncio lock prevents concurrent sub-goal storms (C-5)
_execution_lock = asyncio.Lock()

# =============================================================================
# XML / TOOL-CALL SCRUBBER  (M-1: ReDoS-safe)
# =============================================================================

def scrub_xml_tool_calls(text: str) -> str:
    if not text:
        return ""
    text = text[:8000]
    cleaned = re.sub(r'<function=[^>]*>[^<]{0,2000}</function>', '', text)
    cleaned = re.sub(r'<tool_call>[^<]{0,2000}</tool_call>', '', cleaned)
    cleaned = re.sub(r'</?function[^>]{0,200}>', '', cleaned)
    cleaned = re.sub(r'\{"name"\s*:\s*"tool_[^}]{0,500}\}', '', cleaned)
    return cleaned.strip()

# =============================================================================
# SKILL LIBRARY (ChromaDB + JSON cosine fallback)
# =============================================================================

class SkillLibraryManager:
    def __init__(self, data_dir: str = "./skill_store"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.use_chroma = False
        self.json_file = os.path.join(self.data_dir, "skills.json")
        self.skills: Dict[str, Any] = self._load_json_skills()

        try:
            import chromadb
            self.chroma_client = chromadb.PersistentClient(
                path=os.path.join(self.data_dir, "chroma")
            )
            self.chroma_collection = self.chroma_client.get_or_create_collection(
                "mineflayer_skills"
            )
            self.use_chroma = True
            print("[SkillLibrary] ChromaDB initialized.")
        except Exception as e:
            print(f"[SkillLibrary] ChromaDB unavailable ({e}). Using JSON cosine fallback.")

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
            print(f"[SkillLibrary] Save error: {e}")

    def _to_vec(self, text: str) -> Dict[str, int]:
        vec: Dict[str, int] = {}
        for w in re.findall(r'\w+', text.lower()):
            vec[w] = vec.get(w, 0) + 1
        return vec

    def _cosine(self, v1: Dict[str, int], v2: Dict[str, int]) -> float:
        inter = set(v1) & set(v2)
        num = sum(v1[k] * v2[k] for k in inter)
        denom = math.sqrt(sum(v**2 for v in v1.values())) * math.sqrt(sum(v**2 for v in v2.values()))
        return float(num) / denom if denom else 0.0

    def _normalize_js_name(self, subgoal: str) -> str:
        """Convert a subgoal string to a valid camelCase JavaScript function name."""
        words = re.sub(r'[^\w\s]', '', subgoal.lower()).split()
        if not words:
            return "unknownSkill"
        return (words[0] + "".join(w.capitalize() for w in words[1:]))[:48]

    def add_skill(
        self,
        name: str,
        description: str,
        code: str,
        metadata: Optional[dict] = None,
    ) -> str:
        meta = metadata or {}
        skill_id = re.sub(r'[^\w]', '_', name.lower())[:64]
        js_fn_name = self._normalize_js_name(name)
        record = {
            "id":          skill_id,
            "name":        name,
            "description": description,
            "code":        code,
            "js_fn_name":  js_fn_name,
            "sub_goal":    meta.get("sub_goal", name),
        }
        self.skills[skill_id] = record
        self._save_json_skills()
        if self.use_chroma:
            try:
                self.chroma_collection.upsert(
                    ids=[skill_id],
                    documents=[f"{name} {description}"],
                    metadatas=[{"name": name, "sub_goal": meta.get("sub_goal", name)}],
                )
            except Exception as e:
                print(f"[SkillLibrary] ChromaDB upsert warning: {e}")
        return skill_id

    def query_skill(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        if self.use_chroma:
            try:
                res = self.chroma_collection.query(
                    query_texts=[query], n_results=top_k
                )
                if res and res.get("ids") and res["ids"][0]:
                    hits = [self.skills[sid] for sid in res["ids"][0] if sid in self.skills]
                    if hits:
                        return hits
            except Exception as e:
                print(f"[SkillLibrary] ChromaDB query warning ({e}), falling back.")

        qv = self._to_vec(query)
        scored = []
        for data in self.skills.values():
            dv = self._to_vec(f"{data['name']} {data['description']}")
            scored.append((self._cosine(qv, dv), data))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for s, d in scored if s > 0.05][:top_k]

    def get_all(self) -> List[Dict[str, Any]]:
        return list(self.skills.values())


skill_library = SkillLibraryManager()

# =============================================================================
# GLOBAL STATE — updated with Pillars 1, 2, 4 new keys
# =============================================================================

CURRICULUM_STATE: Dict[str, Any] = {
    # Core (existing)
    "master_goal":        "Survive and thrive: gather wood, make tools, establish shelter, and prepare for progression.",
    "active_subgoal":     None,
    "completed_subgoals": [],
    "failed_subgoals":    [],
    "planner_enabled":    True,
    "executing":          False,
    "last_status":        "Initialized",
    "last_error":         None,
    # Pillar 1 — LLM Curriculum new fields
    "last_rationale":     None,
    "curiosity_score":    0,
    "stale_ticks":        0,
    # Pillar 4 — Critic Agent new fields
    "last_critic_report": None,
}

SYSTEM_PROMPT = (
    "You are Aries, an autonomous AI Minecraft agent inspired by the Voyager framework. "
    "You have a dynamic LLM curriculum planner, a Cartographer spatial memory system, "
    "a RAG skill library with composable helper functions, and a Critic Agent that "
    "diagnoses every failure. "
    "CRITICAL: NEVER output raw XML, function call syntax, or markdown code fences "
    "like `<function=...>`, `<tool_call>`, or ```json in spoken responses. "
    "All in-game actions MUST go through native tool calls. "
    "Speak naturally and concisely. You can query tool_get_telemetry to verify real state "
    "before claiming success."
)

CONVERSATION_HISTORY: List[Dict[str, Any]] = [
    {"role": "system", "content": SYSTEM_PROMPT}
]

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
        return v[:4000]

class MasterGoalRequest(BaseModel):
    master_goal: str

class ExecuteScriptRequest(BaseModel):
    code: str
    timeoutMs: int = 45000

# Pillar 2: World map ingestion model
class MapLogRequest(BaseModel):
    blocks: List[Dict[str, Any]]
    timestamp: float = 0.0

# =============================================================================
# BRIDGE COMMUNICATION HELPERS (C-4: async httpx)
# =============================================================================

async def fetch_telemetry() -> Optional[Dict[str, Any]]:
    try:
        res = await _http_client.get(f"{MC_BRIDGE_URL}/telemetry")
        if res.status_code == 200:
            return res.json().get("telemetry")
    except Exception as e:
        print(f"[Telemetry] Failed: {e}")
    return None


async def execute_js_script(code: str, timeout_ms: int = 45000) -> Dict[str, Any]:
    try:
        res = await _http_client.post(
            f"{MC_BRIDGE_URL}/execute-script",
            json={"code": code, "timeoutMs": timeout_ms},
            timeout=timeout_ms / 1000 + 10,
        )
        return res.json()
    except httpx.TimeoutException:
        return {
            "status": "error",
            "message": f"Bridge timed out after {timeout_ms/1000+10:.0f}s.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Bridge error: {e}"}

# =============================================================================
# TELEMETRY DIFFING ENGINE
# =============================================================================

def extract_target_count(subgoal: str) -> Tuple[int, str]:
    m = re.search(r'(\d+)', subgoal)
    count = int(m.group(1)) if m else 1
    sub = subgoal.lower()
    for kw in [
        "log", "planks", "cobblestone", "stone", "dirt",
        "pickaxe", "axe", "sword", "table", "stick", "iron", "coal",
    ]:
        if kw in sub:
            return count, kw
    return count, "item"


def verify_telemetry_diff(
    pre: Optional[Dict[str, Any]],
    post: Optional[Dict[str, Any]],
    subgoal: str,
) -> Tuple[bool, str]:
    if not pre or not post:
        return False, "CRITICAL: Telemetry lost — Node bridge may be down."

    target, kw = extract_target_count(subgoal)
    pre_inv  = {i['name']: i['count'] for i in pre.get('inventory', [])}
    post_inv = {i['name']: i['count'] for i in post.get('inventory', [])}

    if kw != "item":
        pre_sum  = sum(c for n, c in pre_inv.items()  if kw in n)
        post_sum = sum(c for n, c in post_inv.items() if kw in n)
        gained   = post_sum - pre_sum
        if gained >= target:
            return True, f"Diff verified: +{gained} {kw} (needed {target})."
        if post_sum >= target:
            return True, f"Already holding {post_sum} {kw} (target {target})."
        return False, f"Diff FAILED: gained +{gained} {kw} (total {post_sum}/{target})."

    sub = subgoal.lower()
    if any(w in sub for w in ("move", "go to", "walk", "travel", "explore")):
        pp = pre.get('position', {})
        qp = post.get('position', {})
        dist = math.sqrt(
            (qp.get('x', 0) - pp.get('x', 0))**2 +
            (qp.get('z', 0) - pp.get('z', 0))**2
        )
        if dist > 3.0:
            return True, f"Moved {dist:.1f} blocks."
        return False, f"Bot barely moved ({dist:.1f} blocks)."

    return True, "Script executed without errors."

# =============================================================================
# ███████████████████████████████████████████████████████████████████████████
# PILLAR 4 — CRITIC AGENT (llama-3.3-70b-versatile)
# ███████████████████████████████████████████████████████████████████████████
# =============================================================================

async def run_critic_agent(
    subgoal: str,
    failed_code: str,
    error_message: str,
    pre_telemetry: Optional[Dict[str, Any]],
    post_telemetry: Optional[Dict[str, Any]],
    attempt_number: int,
) -> Dict[str, Any]:
    """
    Sends a rich contextual failure to the 70b Critic model and receives a
    structured JSON diagnosis used to steer the next code generation attempt.

    Output schema:
      {
        "failure_reason":           str,   # root-cause diagnosis
        "suggested_fix":            str,   # concrete code change hint
        "avoid_patterns":           [str], # specific patterns to ban in rewrite
        "is_recoverable":           bool,  # if False, skip remaining retries
        "requires_different_approach": bool
      }
    """
    pre_str  = json.dumps(pre_telemetry,  indent=2)[:2000] if pre_telemetry  else "unavailable"
    post_str = json.dumps(post_telemetry, indent=2)[:2000] if post_telemetry else "unavailable"

    prompt = f"""You are a Mineflayer JavaScript expert conducting a code failure post-mortem.
A Minecraft bot script failed to achieve a goal. Diagnose the EXACT root cause.

═══ SUBGOAL ═══
{subgoal}

═══ ATTEMPT ═══
Attempt #{attempt_number}

═══ FAILED JAVASCRIPT ═══
```javascript
{failed_code[:3000]}
```

═══ ERROR / STACK TRACE ═══
{error_message[:2000]}

═══ PRE-EXECUTION TELEMETRY ═══
{pre_str}

═══ POST-EXECUTION TELEMETRY ═══
{post_str}

═══ DIAGNOSTIC CHECKLIST ═══
Check these failure modes in order:
1. Wrong Minecraft item ID (e.g., "plank" instead of "oak_planks")?
2. Missing await on async Mineflayer function?
3. Tried to use item not in inventory?
4. Pathfinder target unreachable (void, underwater, out-of-bounds)?
5. Recipe requires crafting table but none nearby?
6. Tool too weak to break target block (e.g., hand on stone)?
7. Bot died mid-execution (health 0 in post-telemetry)?
8. collectBlock.collect() called on wrong block object?
9. Sandbox access to blocked bot property (_client, emit, etc.)?
10. Code length or syntax error stopped compilation?

Respond with EXACTLY this JSON (no markdown, no prose outside the JSON):
{{
  "failure_reason": "<1-2 sentence precise root cause>",
  "suggested_fix": "<specific actionable code change to fix the root cause>",
  "avoid_patterns": ["<code pattern to avoid>", "<another pattern>"],
  "is_recoverable": true,
  "requires_different_approach": false
}}"""

    try:
        # Use 70b model for deep reasoning — run in thread to avoid blocking event loop
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Mineflayer JavaScript debugging specialist. "
                        "Output only valid JSON matching the specified schema exactly."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,   # Low temp → deterministic, logical diagnosis
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content.strip())
        # Ensure required keys exist
        result.setdefault("failure_reason", error_message[:300])
        result.setdefault("suggested_fix", "Retry with corrected item IDs and await keywords.")
        result.setdefault("avoid_patterns", [])
        result.setdefault("is_recoverable", True)
        result.setdefault("requires_different_approach", False)
        return result
    except Exception as e:
        print(f"[CriticAgent] LLM call failed: {e}")
        return {
            "failure_reason": error_message[:400],
            "suggested_fix":  "Retry with explicit item ID validation and await on all async calls.",
            "avoid_patterns": [],
            "is_recoverable": True,
            "requires_different_approach": False,
        }

# =============================================================================
# ███████████████████████████████████████████████████████████████████████████
# PILLAR 3 — RAG SKILL COMPOSITION + CODE GENERATION
# ███████████████████████████████████████████████████████████████████████████
# =============================================================================

async def generate_js_code(
    subgoal: str,
    telemetry: Optional[Dict[str, Any]],
    critic_context: Optional[Dict[str, Any]] = None,  # Pillar 4: structured critique
    failed_code: Optional[str] = None,
) -> str:
    """
    Generates Mineflayer JS for a subgoal.

    Pillar 3 (RAG): Retrieves top-2 relevant skills from the vector library
    and pre-injects them as callable async helper functions into the generated code.

    Pillar 4 (Critic): If critic_context is provided, replaces the raw error
    string with a rich structured diagnosis that steers the rewrite.
    """
    telemetry_str = json.dumps(telemetry, indent=2)[:2000] if telemetry else "Unavailable"

    # ── Pillar 3: RAG skill retrieval & injection ──────────────────────────
    relevant_skills = skill_library.query_skill(subgoal, top_k=2)
    skill_header = ""
    skill_hint_lines: List[str] = []

    if relevant_skills:
        skill_header = "// ═══ INJECTED SKILLS (call these — don't reimplement) ═══\n"
        for skill in relevant_skills:
            fn_name = skill.get("js_fn_name") or re.sub(r'[^\w]', '_', skill['name'].lower())[:48]
            skill_header += (
                f"async function {fn_name}() {{\n"
                f"  // Learned skill: {skill['description']}\n"
                f"  {skill['code']}\n"
                f"}}\n\n"
            )
            skill_hint_lines.append(
                f"  - await {fn_name}()  ← \"{skill['description']}\""
            )

    skill_hint = ""
    if skill_hint_lines:
        skill_hint = (
            "AVAILABLE PRE-BUILT HELPERS (call these instead of reimplementing):\n"
            + "\n".join(skill_hint_lines) + "\n\n"
        )

    # ── Pillar 4: Critic context block ────────────────────────────────────
    critic_block = ""
    if critic_context and failed_code:
        avoid_list = "\n".join(
            f"  ✗ {p}" for p in critic_context.get("avoid_patterns", [])
        )
        critic_block = f"""
═══ CRITIC AGENT DIAGNOSIS (study this before rewriting) ═══
Root cause:     {critic_context.get('failure_reason', 'Unknown')}
Fix directive:  {critic_context.get('suggested_fix', 'None')}
Patterns to AVOID in your rewrite:
{avoid_list or '  (none specified)'}

Previously failed code (do NOT repeat verbatim):
```javascript
{failed_code[:1800]}
```
"""

    # ── Prompt construction ────────────────────────────────────────────────
    if critic_context and failed_code:
        prompt_body = f"""You are rewriting a failed Mineflayer script.
{critic_block}
{skill_hint}
Current Bot Telemetry:
{telemetry_str}

Subgoal to accomplish: {subgoal}

Rules:
1. Output ONLY raw JavaScript — no markdown backticks, no explanations, no comments beyond inline.
2. Use await on ALL async Mineflayer operations.
3. Use exact Minecraft item IDs (oak_planks, oak_log, stone_pickaxe, crafting_table).
4. Validate inventory before using items: const item = bot.inventory.items().find(i => i.name === 'x'); if (!item) throw new Error('x not in inventory');
5. If calling a HELPER FUNCTION, call it with: await helperName();
6. Do NOT use fetch() — it is blocked in the sandbox.
"""
    else:
        prompt_body = f"""You are writing a self-contained Mineflayer JavaScript snippet.

Subgoal: {subgoal}

{skill_hint}
Available sandbox variables:
  bot       — Mineflayer bot instance (whitelisted proxy)
  Vec3      — vec3 constructor
  pathfinder, goals, Movements — mineflayer-pathfinder
  mcData    — minecraft-data instance
  normalizeItemName — item alias normalizer
  console   — custom logger (log/error/warn)

Current Bot Telemetry:
{telemetry_str}

Rules:
1. Output ONLY raw JavaScript — no markdown backticks, no explanations.
2. Use await on ALL async Mineflayer operations.
3. Use exact Minecraft item IDs (oak_planks, oak_log, stone_pickaxe, crafting_table, etc).
4. Validate inventory before using items: const item = bot.inventory.items().find(i => i.name === 'x'); if (!item) throw new Error('x not in inventory');
5. If calling a HELPER FUNCTION, call it with: await helperName();
6. Do NOT use fetch() — it is blocked in the sandbox.
7. For crafting, use: const r = bot.recipesFor(mcData.itemsByName['oak_planks'].id, null, 1, false); if (r.length) await bot.craft(r[0], 4, null);
"""

    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Output strictly raw executable Mineflayer JavaScript. No markdown. No backticks. No prose.",
                },
                {"role": "user", "content": prompt_body},
            ],
            temperature=0.15,
            max_tokens=2048,
        )
        code = response.choices[0].message.content.strip()
        # Strip accidental fences
        if code.startswith("```"):
            lines = code.splitlines()
            lines = lines[1:] if lines[0].startswith("```") else lines
            lines = lines[:-1] if lines and lines[-1].startswith("```") else lines
            code = "\n".join(lines).strip()

        # Pillar 3: Prepend injected skill helpers to the generated code
        return skill_header + code if skill_header else code

    except Exception as e:
        print(f"[CodeGen] Error: {e}")
        return "// Code generation failed — LLM error."

# =============================================================================
# ███████████████████████████████████████████████████████████████████████████
# CORE EXECUTION LOOP — wires Pillars 3 & 4 together
# ███████████████████████████████████████████████████████████████████████████
# =============================================================================

async def run_iterative_execution_loop(subgoal: str) -> bool:
    """
    Executes a single subgoal through up to 4 attempts.
    - Attempt 1: Tries cached skill OR generates fresh code
    - Attempt N>1: Runs Critic Agent, then regenerates with structured diagnosis
    - On success: saves the winning code to the skill library
    - On unrecoverable failure: short-circuits remaining attempts
    """
    async with _execution_lock:   # C-5: only one subgoal runs at a time
        print(f"\n[ExecLoop] ═══ Starting: '{subgoal}' ═══")
        CURRICULUM_STATE["executing"]      = True
        CURRICULUM_STATE["active_subgoal"] = subgoal
        CURRICULUM_STATE["last_status"]    = f"Executing: '{subgoal}'"

        # Skill library cache lookup (Pillar 3)
        existing = skill_library.query_skill(subgoal, top_k=1)
        attempt_code: Optional[str] = existing[0]["code"] if existing else None
        if attempt_code:
            print(f"[ExecLoop] Reusing skill: '{existing[0]['name']}'")

        critic_context: Optional[Dict[str, Any]] = None  # grows richer each failure
        max_attempts = 4
        success = False

        for attempt in range(1, max_attempts + 1):
            print(f"[ExecLoop] Attempt {attempt}/{max_attempts}")

            pre_telemetry = await fetch_telemetry()

            # (Re)generate code if this is a retry or no cached skill
            if not attempt_code or attempt > 1:
                attempt_code = await generate_js_code(
                    subgoal=subgoal,
                    telemetry=pre_telemetry,
                    critic_context=critic_context,   # Pillar 4: pass structured critique
                    failed_code=attempt_code,
                )

            res           = await execute_js_script(attempt_code, timeout_ms=45000)
            post_telemetry = await fetch_telemetry()

            if res.get("status") == "success":
                verified, diff_msg = verify_telemetry_diff(
                    pre_telemetry, post_telemetry, subgoal
                )
                if verified:
                    print(f"[ExecLoop] ✓ SUCCESS '{subgoal}' — {diff_msg}")
                    skill_library.add_skill(
                        name=subgoal,
                        description=f"Mineflayer script for: {subgoal}",
                        code=attempt_code,
                        metadata={"sub_goal": subgoal},
                    )
                    CURRICULUM_STATE["completed_subgoals"].append(subgoal)
                    CURRICULUM_STATE["last_status"]    = f"Completed: '{subgoal}'"
                    CURRICULUM_STATE["last_error"]     = None
                    CURRICULUM_STATE["last_critic_report"] = None
                    success = True
                    break
                else:
                    error_msg = f"Telemetry diff failed: {diff_msg}"
            else:
                error_msg = res.get("message") or res.get("logs") or "Unknown execution error."

            print(f"[ExecLoop] ✗ Attempt {attempt} failed: {error_msg}")
            CURRICULUM_STATE["last_error"] = f"Attempt {attempt}: {error_msg}"

            # ── Pillar 4: Run Critic Agent before every retry (except the last) ──
            if attempt < max_attempts:
                print(f"[Critic] Diagnosing attempt {attempt} failure...")
                critic_context = await run_critic_agent(
                    subgoal=subgoal,
                    failed_code=attempt_code,
                    error_message=error_msg,
                    pre_telemetry=pre_telemetry,
                    post_telemetry=post_telemetry,
                    attempt_number=attempt,
                )
                CURRICULUM_STATE["last_critic_report"] = critic_context
                print(f"[Critic] Root cause: {critic_context.get('failure_reason')}")
                print(f"[Critic] Fix:        {critic_context.get('suggested_fix')}")

                # Short-circuit if Critic marks the error as unrecoverable
                if not critic_context.get("is_recoverable", True):
                    print(f"[Critic] ⚠ Marked unrecoverable — skipping remaining attempts.")
                    break

            await asyncio.sleep(2)

        if not success:
            print(f"[ExecLoop] ✗ FAILED all attempts for '{subgoal}'")
            CURRICULUM_STATE["failed_subgoals"].append(subgoal)
            CURRICULUM_STATE["last_status"] = f"Failed: '{subgoal}'"

        CURRICULUM_STATE["active_subgoal"] = None
        CURRICULUM_STATE["executing"]      = False
        return success

# =============================================================================
# ███████████████████████████████████████████████████████████████████████████
# PILLAR 1 — LLM-DRIVEN CURRICULUM PLANNER (llama-3.1-8b-instant)
# ███████████████████████████████████████████████████████████████████████████
# =============================================================================

def determine_next_subgoal_heuristic(
    telemetry: Optional[Dict[str, Any]]
) -> str:
    """
    Preserved deterministic fallback — used when the LLM Curriculum call fails.
    Renamed from the original determine_next_subgoal().
    """
    if not telemetry:
        return "Collect 3 oak_log"

    inv    = {i['name']: i['count'] for i in telemetry.get('inventory', [])}
    health = telemetry.get('health', 20)

    if health < 8:
        return "Eat food or find safe shelter"

    logs    = sum(c for n, c in inv.items() if 'log'            in n)
    planks  = sum(c for n, c in inv.items() if 'planks'         in n)
    ctables = sum(c for n, c in inv.items() if 'crafting_table' in n)
    sticks  = sum(c for n, c in inv.items() if 'stick'          in n)
    picks   = [n for n in inv if 'pickaxe' in n]
    stone   = sum(c for n, c in inv.items() if 'cobblestone' in n or ('stone' in n and 'pickaxe' not in n))
    iron    = sum(c for n, c in inv.items() if 'iron' in n or 'raw_iron' in n)

    if logs == 0 and planks == 0 and ctables == 0:    return "Collect 5 oak_log"
    if logs > 0 and planks < 4 and ctables == 0:      return "Craft 8 oak_planks from oak_log"
    if planks >= 4 and ctables == 0:                  return "Craft 1 crafting_table"
    if ctables > 0 and sticks < 4 and not picks:      return "Craft 4 sticks"
    if ctables > 0 and sticks >= 2 and planks >= 3 and not picks: return "Craft 1 wooden_pickaxe"
    if 'wooden_pickaxe' in picks and stone < 3:       return "Collect 3 stone"
    if stone >= 3 and 'stone_pickaxe' not in picks:   return "Craft 1 stone_pickaxe"
    if 'stone_pickaxe' in picks and iron < 3:         return "Collect 3 iron_ore"
    return "Explore surroundings and collect resources"


async def determine_next_subgoal_llm(
    master_goal:      str,
    telemetry:        Optional[Dict[str, Any]],
    completed:        List[str],
    failed:           List[str],
    world_summary:    str = "",
    last_rationale:   Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pillar 1 — LLM Curriculum Planner.

    Uses llama-3.1-8b-instant (fast, low-cost) for high-frequency planning ticks.
    Returns:
      {
        "subgoal":              str,
        "rationale":            str,
        "curiosity_score":      int (0–10),
        "estimated_difficulty": str ("easy"|"medium"|"hard")
      }
    Gracefully falls back to the heuristic on any LLM error.
    """
    inv_str = json.dumps(
        {i["name"]: i["count"] for i in telemetry.get("inventory", [])},
        indent=2,
    ) if telemetry else "unknown"

    prompt = f"""You are the curriculum planner for Aries, an autonomous Minecraft agent.
Choose ONE concrete next action given the world state below.

═══ MASTER GOAL ═══
{master_goal}

═══ BOT STATE ═══
Health:     {telemetry.get("health", "?")}/20
Food:       {telemetry.get("food",   "?")}/20
Biome:      {telemetry.get("biome",  "unknown")}
Time:       {telemetry.get("timeOfDay", "?")} (0=dawn, 6000=noon, 12000=dusk, 18000=night)
Raining:    {telemetry.get("isRaining", False)}
Position:   {json.dumps(telemetry.get("position", {}))}

═══ INVENTORY ═══
{inv_str}

═══ NEARBY ENTITIES ═══
{json.dumps(telemetry.get("nearbyEntities", [])[:8], indent=2) if telemetry else "unknown"}

═══ SPATIAL MEMORY (Cartographer) ═══
{world_summary or "No world map data yet — encourage exploration to discover resources."}

═══ COMPLETED SUBGOALS (last 15) ═══
{json.dumps(completed[-15:], indent=2)}

═══ RECENTLY FAILED SUBGOALS (avoid re-attempting without new context) ═══
{json.dumps(failed[-8:], indent=2)}

═══ PREVIOUS RATIONALE ═══
{last_rationale or "None — this is the first planning tick."}

═══ MINECRAFT TECH TREE CONTEXT ═══
Survival: wood → planks → crafting_table → wooden_pickaxe → stone → stone_pickaxe → iron ore → iron ingots → iron_pickaxe → diamonds → enchanting table → nether portal → blaze rods → eyes of ender → stronghold → End → Ender Dragon.
Priority chain: health > food > shelter > basic tools > tech progression > exploration > Ender Dragon.

═══ REASONING TASK ═══
Think step-by-step:
1. What is the most urgent survival need (if any)?
2. What is the current tech-tree position based on inventory?
3. What is the most logical next crafting or collection step?
4. Has the agent been stagnant? (Assign curiosity_score 7-10 if no progress visible.)
5. Are there known resources in the spatial memory that should be used?

Assign curiosity_score:
  0-3  → Clear crafting/collection goal with known resources
  4-6  → Moderate uncertainty, some exploration useful
  7-10 → Stagnant progress, agent must explore to discover new resources

Respond with ONLY this JSON (no markdown, no prose):
{{
  "subgoal": "<single, atomic, executable subgoal — specific enough to write Mineflayer code for>",
  "rationale": "<1-2 sentence reasoning>",
  "curiosity_score": <integer 0-10>,
  "estimated_difficulty": "<easy|medium|hard>"
}}"""

    try:
        # llama-3.1-8b-instant: fast model for high-frequency curriculum ticks
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Minecraft AI curriculum planner. "
                        "Output only valid JSON matching the exact schema requested."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content.strip())
        assert isinstance(result.get("subgoal"), str) and result["subgoal"].strip()
        result.setdefault("rationale",            "LLM did not provide rationale.")
        result.setdefault("curiosity_score",      3)
        result.setdefault("estimated_difficulty", "medium")
        return result

    except Exception as e:
        print(f"[CurriculumLLM] Failed ({e}), using heuristic fallback.")
        fallback = determine_next_subgoal_heuristic(telemetry)
        return {
            "subgoal":              fallback,
            "rationale":            f"LLM unavailable — heuristic fallback. Error: {e}",
            "curiosity_score":      0,
            "estimated_difficulty": "easy",
        }


async def curriculum_planner_loop():
    """
    Background planning loop. Runs every 12 seconds.
    Uses LLM curriculum planner with curiosity-driven exploration and stale-tick detection.
    """
    EXPLORATION_CURIOSITY_THRESHOLD = 6
    STALE_TICK_THRESHOLD            = 6   # ticks with no inventory change → forced explore

    print("[CurriculumPlanner] Background loop active.")
    await asyncio.sleep(5)

    last_inv_snapshot: Dict[str, int] = {}

    while True:
        try:
            if CURRICULUM_STATE["planner_enabled"] and not _execution_lock.locked():
                telemetry = await fetch_telemetry()

                if telemetry:
                    # ── Stale-tick detection ──────────────────────────────
                    current_inv = {
                        i["name"]: i["count"] for i in telemetry.get("inventory", [])
                    }
                    if current_inv == last_inv_snapshot:
                        CURRICULUM_STATE["stale_ticks"] += 1
                    else:
                        CURRICULUM_STATE["stale_ticks"] = 0
                        last_inv_snapshot = dict(current_inv)

                    stale = CURRICULUM_STATE["stale_ticks"]

                    # ── Pillar 2: Build world map context for planner ──────
                    bot_pos = telemetry.get("position")
                    world_summary = world_map.summarize_for_llm(
                        from_pos=bot_pos, max_entries=18
                    )

                    # ── Pillar 1: LLM Curriculum decides next subgoal ──────
                    decision = await determine_next_subgoal_llm(
                        master_goal=CURRICULUM_STATE["master_goal"],
                        telemetry=telemetry,
                        completed=CURRICULUM_STATE["completed_subgoals"],
                        failed=CURRICULUM_STATE["failed_subgoals"],
                        world_summary=world_summary,
                        last_rationale=CURRICULUM_STATE.get("last_rationale"),
                    )

                    next_subgoal   = decision["subgoal"]
                    curiosity      = decision["curiosity_score"]
                    CURRICULUM_STATE["last_rationale"]  = decision["rationale"]
                    CURRICULUM_STATE["curiosity_score"] = curiosity

                    # ── Curiosity / stale override → force exploration ─────
                    if curiosity >= EXPLORATION_CURIOSITY_THRESHOLD or stale >= STALE_TICK_THRESHOLD:
                        direction = ["north", "south", "east", "west"][stale % 4]
                        next_subgoal = (
                            f"Explore {direction} for 80 blocks, logging any new biomes, "
                            f"ores, or structures discovered."
                        )
                        print(
                            f"[Planner] Curiosity={curiosity}, stale={stale} → "
                            f"overriding to: '{next_subgoal}'"
                        )
                        CURRICULUM_STATE["stale_ticks"] = 0  # reset after exploration trigger

                    # ── Schedule only if not already completed or failed ───
                    if (
                        next_subgoal
                        and next_subgoal not in CURRICULUM_STATE["completed_subgoals"]
                        and next_subgoal not in CURRICULUM_STATE["failed_subgoals"]
                    ):
                        print(f"[Planner] Scheduling: '{next_subgoal}' | rationale: {decision['rationale'][:80]}")
                        asyncio.create_task(run_iterative_execution_loop(next_subgoal))

        except Exception as e:
            print(f"[CurriculumPlanner Error] {e}")

        await asyncio.sleep(12)  # Slightly longer tick to amortize LLM call latency


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(curriculum_planner_loop())

# =============================================================================
# ASYNC TOOL FUNCTIONS  (C-4: all bridge calls via httpx)
# =============================================================================

async def tool_move(x: float, y: float, z: float):
    r = await _http_client.post(f"{MC_BRIDGE_URL}/move", json={"x": x, "y": y, "z": z})
    return r.json()

async def tool_collect(blockName: str, count: int = 1):
    r = await _http_client.post(f"{MC_BRIDGE_URL}/collect", json={"blockName": blockName, "count": count})
    return r.json()

async def tool_place(blockName: str, x: float, y: float, z: float):
    r = await _http_client.post(f"{MC_BRIDGE_URL}/place", json={"blockName": blockName, "x": x, "y": y, "z": z})
    return r.json()

async def tool_deposit(itemName: str, count: Optional[int] = None):
    r = await _http_client.post(f"{MC_BRIDGE_URL}/deposit", json={"itemName": itemName, "count": count})
    return r.json()

async def tool_craft(itemName: str, count: int = 1):
    r = await _http_client.post(f"{MC_BRIDGE_URL}/craft", json={"itemName": itemName, "count": count})
    return r.json()

async def tool_fight(mobName: str):
    r = await _http_client.post(f"{MC_BRIDGE_URL}/fight", json={"mobName": mobName})
    return r.json()

async def tool_pvp():
    r = await _http_client.post(f"{MC_BRIDGE_URL}/pvp")
    return r.json()

async def tool_get_inventory():
    r = await _http_client.get(f"{MC_BRIDGE_URL}/inventory")
    return r.json()

async def tool_stop():
    r = await _http_client.delete(f"{MC_BRIDGE_URL}/stop")
    return r.json()

async def tool_save_me(mobName: Optional[str] = None):
    await _http_client.delete(f"{MC_BRIDGE_URL}/stop")
    if mobName:
        r = await _http_client.post(f"{MC_BRIDGE_URL}/fight", json={"mobName": mobName})
    else:
        r = await _http_client.post(f"{MC_BRIDGE_URL}/pvp")
    return {"status": "success", "message": f"Emergency engaged against {mobName or 'nearest threat'}!"}

async def tool_look_around():
    r = await _http_client.get(f"{MC_BRIDGE_URL}/surroundings")
    return r.json()

async def tool_break_block(
    x: Optional[float] = None,
    y: Optional[float] = None,
    z: Optional[float] = None,
    blockName: Optional[str] = None,
    block: Optional[str] = None,
    count: int = 1,
):
    target = blockName or block or "oak_log"
    if x is not None and y is not None and z is not None and not (x == 0 and y == 0 and z == 0):
        r = await _http_client.post(f"{MC_BRIDGE_URL}/break-at", json={"x": x, "y": y, "z": z})
    else:
        r = await _http_client.post(f"{MC_BRIDGE_URL}/collect", json={"blockName": target, "count": count})
    return r.json()

async def tool_query_skills(query: str):
    return {"status": "success", "skills": skill_library.query_skill(query)}

async def tool_save_skill(name: str, description: str, code: str):
    sid = skill_library.add_skill(name, description, code)
    return {"status": "success", "skill_id": sid}

async def tool_get_telemetry():
    t = await fetch_telemetry()
    return {"status": "success", "telemetry": t}

async def tool_set_master_goal(master_goal: str):
    CURRICULUM_STATE["master_goal"] = master_goal
    CURRICULUM_STATE["failed_subgoals"] = []  # fresh slate on new goal
    return {"status": "success", "message": f"Master goal updated: {master_goal}"}

# Pillar 2: World map tool exposed to the LLM agent
async def tool_query_world_map(poi_type: str, limit: int = 5):
    """Query the Cartographer for known locations of a specific block type."""
    telemetry = await fetch_telemetry()
    pos = telemetry.get("position", {"x": 0, "y": 64, "z": 0}) if telemetry else {"x": 0, "y": 64, "z": 0}
    results = world_map.query_nearest(poi_type, pos, max_results=min(limit, 10))
    return {
        "status":   "success",
        "poi_type": poi_type,
        "count":    len(results),
        "results":  results,
    }


AVAILABLE_TOOLS: Dict[str, Any] = {
    "tool_move":             tool_move,
    "tool_collect":          tool_collect,
    "tool_place":            tool_place,
    "tool_deposit":          tool_deposit,
    "tool_craft":            tool_craft,
    "tool_fight":            tool_fight,
    "tool_pvp":              tool_pvp,
    "tool_get_inventory":    tool_get_inventory,
    "tool_stop":             tool_stop,
    "tool_save_me":          tool_save_me,
    "tool_look_around":      tool_look_around,
    "tool_break_block":      tool_break_block,
    "tool_query_skills":     tool_query_skills,
    "tool_save_skill":       tool_save_skill,
    "tool_get_telemetry":    tool_get_telemetry,
    "tool_set_master_goal":  tool_set_master_goal,
    "tool_query_world_map":  tool_query_world_map,
}

GROQ_TOOLS = [
    {"type":"function","function":{"name":"tool_move","description":"Move the bot to X, Y, Z.","parameters":{"type":"object","properties":{"x":{"type":"number"},"y":{"type":"number"},"z":{"type":"number"}},"required":["x","y","z"]}}},
    {"type":"function","function":{"name":"tool_collect","description":"Collect/mine a block type like oak_log, coal_ore, stone.","parameters":{"type":"object","properties":{"blockName":{"type":"string"},"count":{"type":"integer"}},"required":["blockName"]}}},
    {"type":"function","function":{"name":"tool_place","description":"Place a block at X, Y, Z.","parameters":{"type":"object","properties":{"blockName":{"type":"string"},"x":{"type":"number"},"y":{"type":"number"},"z":{"type":"number"}},"required":["blockName","x","y","z"]}}},
    {"type":"function","function":{"name":"tool_deposit","description":"Deposit items into a nearby chest.","parameters":{"type":"object","properties":{"itemName":{"type":"string"},"count":{"type":"integer"}},"required":["itemName"]}}},
    {"type":"function","function":{"name":"tool_craft","description":"Craft items. Use exact IDs: oak_planks, sticks, wooden_pickaxe.","parameters":{"type":"object","properties":{"itemName":{"type":"string"},"count":{"type":"integer"}},"required":["itemName"]}}},
    {"type":"function","function":{"name":"tool_fight","description":"Fight a specific mob type.","parameters":{"type":"object","properties":{"mobName":{"type":"string"}},"required":["mobName"]}}},
    {"type":"function","function":{"name":"tool_pvp","description":"Engage the nearest hostile mob."}},
    {"type":"function","function":{"name":"tool_get_inventory","description":"Check current inventory."}},
    {"type":"function","function":{"name":"tool_stop","description":"Stop all current bot actions immediately."}},
    {"type":"function","function":{"name":"tool_save_me","description":"Emergency: stop everything and fight nearest threat.","parameters":{"type":"object","properties":{"mobName":{"type":"string"}},"required":[]}}},
    {"type":"function","function":{"name":"tool_look_around","description":"Scan nearby blocks and entities."}},
    {"type":"function","function":{"name":"tool_query_skills","description":"Search the vector skill library for relevant learned code.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"tool_get_telemetry","description":"Get full bot state: health, food, position, inventory, biome, nearby entities."}},
    {"type":"function","function":{"name":"tool_set_master_goal","description":"Set a new top-level master goal for the curriculum planner.","parameters":{"type":"object","properties":{"master_goal":{"type":"string"}},"required":["master_goal"]}}},
    {"type":"function","function":{"name":"tool_query_world_map","description":"Query the Cartographer spatial memory for known locations of a block type (e.g. iron_ore, chest, crafting_table).","parameters":{"type":"object","properties":{"poi_type":{"type":"string"},"limit":{"type":"integer"}},"required":["poi_type"]}}},
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
    CURRICULUM_STATE["master_goal"]      = req.master_goal
    CURRICULUM_STATE["failed_subgoals"]  = []
    CURRICULUM_STATE["stale_ticks"]      = 0
    return {"status": "success", "master_goal": req.master_goal}


@app.get("/agent/skills")
def get_skills():
    return {"status": "success", "skills": skill_library.get_all()}


@app.get("/agent/telemetry")
async def get_telemetry_endpoint():
    t = await fetch_telemetry()
    if not t:
        raise HTTPException(status_code=503, detail="Bridge server telemetry unavailable.")
    return {"status": "success", "telemetry": t}


@app.post("/agent/execute-script")
async def execute_script_endpoint(req: ExecuteScriptRequest):
    return await execute_js_script(req.code, req.timeoutMs)


# ── Pillar 2: World Map endpoints ────────────────────────────────────────────

@app.post("/agent/map/log")
async def map_log_endpoint(req: MapLogRequest):
    """Ingests landmark block discoveries from the Node bridge Cartographer."""
    count = world_map.log_batch(req.blocks, timestamp=req.timestamp or None)
    return {"status": "success", "logged": count, "total": world_map._total_logged}


@app.get("/agent/map/query")
async def map_query_endpoint(
    poi_type: str,
    x: float = 0.0,
    y: float = 64.0,
    z: float = 0.0,
    limit: int = 5,
):
    """Query Cartographer for nearest POIs of a given type."""
    results = world_map.query_nearest(poi_type, {"x": x, "y": y, "z": z}, max_results=limit)
    return {"status": "success", "poi_type": poi_type, "results": results}


@app.get("/agent/map/all")
async def map_all_endpoint():
    """Return full spatial memory state for dashboard rendering."""
    return {"status": "success", "map": world_map.get_all()}


# ── Chat endpoint ─────────────────────────────────────────────────────────────

@app.post("/agent/chat")
async def agent_chat(req: NaturalLanguagePrompt):
    """
    Natural language → tool dispatch → scrubbed reply.
    M-5: Snapshot/rollback pattern protects conversation history on error.
    """
    global CONVERSATION_HISTORY
    snapshot = list(CONVERSATION_HISTORY)
    snapshot.append({"role": "user", "content": req.prompt})

    if len(snapshot) > 13:
        snapshot = [snapshot[0]] + snapshot[-12:]

    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=snapshot,
            tools=GROQ_TOOLS,
            tool_choice="auto",
        )

        msg        = response.choices[0].message
        tool_calls = msg.tool_calls
        executed   : List[str] = []

        if tool_calls:
            snapshot.append(msg)
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    fn_args = {}
                if not isinstance(fn_args, dict):
                    fn_args = {}

                if fn_name in AVAILABLE_TOOLS:
                    result = await AVAILABLE_TOOLS[fn_name](**fn_args)
                    executed.append(fn_name)
                    snapshot.append({
                        "tool_call_id": tc.id,
                        "role":         "tool",
                        "name":         fn_name,
                        "content":      json.dumps(result),
                    })

            second = await asyncio.to_thread(
                client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                messages=snapshot,
            )
            raw = second.choices[0].message.content or ""
        else:
            raw = msg.content or ""

        final = scrub_xml_tool_calls(raw)
        if not final and executed:
            final = f"Executed {', '.join(executed)} successfully."

        snapshot.append({"role": "assistant", "content": final})
        CONVERSATION_HISTORY = snapshot  # M-5: commit only on full success

        return {"status": "success", "response": final, "tools_executed": executed}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))