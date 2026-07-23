# 🌌 ARIES // Autonomous Minecraft Agent

![Node.js](https://img.shields.io/badge/Node.js-339933?style=for-the-badge&logo=nodedotjs&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Tailwind](https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-F55036?style=for-the-badge&logo=groq&logoColor=white)

**ARIES** (Autonomous Reactive Intelligent Embodied System) is a dual-server cognitive architecture for Minecraft, built to mirror and surpass the [MineDojo Voyager](https://github.com/MineDojo/Voyager) framework.

It couples a high-frequency **Node.js/Mineflayer bridge** with an asynchronous **Python/FastAPI brain** powered by Groq LLMs. ARIES dynamically generates JavaScript subroutines, executes them in an isolated Node `vm` sandbox, mathematically verifies state changes, diagnoses execution failures via an LLM Critic, and indexes reusable routines in a vector-backed skill store.

---

## 📂 Project Structure

```text
.
├── brain/
│   ├── skill_store/
│   │   └── skills.json         # Master skill index (code + docstring descriptions)
│   ├── templates/
│   │   └── index.html          # STT & Real-Time Telemetry Web Control Center
│   ├── main.py                 # FastAPI orchestrator, Curriculum Loop & Critic Agent
│   ├── requirements.txt        # Python dependencies (httpx, fastapi, uvicorn, groq, etc.)
│   └── world_map.py            # Cartographer spatial memory & chunk-indexed POI store
├── mc_bridge/
│   ├── bot.js                  # Hardened Mineflayer bot, VM sandbox & primitive wrappers
│   ├── package-lock.json
│   └── package.json            # Node.js dependencies
├── .gitignore
├── README.md
└── voyager_architecture.md     # Reverse-engineering blueprint of MineDojo Voyager

```

---

## ⚡ Cognitive Architecture & Voyager Parity

ARIES implements the four core cognitive pillars of the Voyager specification, optimized for low latency and zero-crash process safety:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                             ARIES BRAIN                                  │
│                                                                          │
│   ┌────────────────────┐    Subgoal & Code    ┌──────────────────────┐   │
│   │ Curriculum Planner │ ───────────────────► │ Execution Engine     │   │
│   │(llama-3.1-8b)      │                      │ (Async httpx + Lock) │   │
│   └─────────▲──────────┘                      └──────────┬───────────┘   │
│             │                                            │               │
│             │ State & Summary                            │ JS Script     │
│             │                                            ▼               │
│   ┌─────────┴──────────┐   Critique / Fix     ┌──────────────────────┐   │
│   │    Cartographer    │ ◄─────────────────── │    Critic Agent      │   │
│   │   (world_map.py)   │                      │ (llama-3.3-70b)      │   │
│   └────────────────────┘                      └──────────────────────┘   │
│             ▲                                            │               │
│             │ 7x7x7 Scans                                │ Verified Code │
│             │                                            ▼               │
│   ┌─────────┴──────────┐                      ┌──────────────────────┐   │
│   │   Node.js Bridge   │ ───────────────────► │  Dual-Vector Skill   │   │
│   │ (Mineflayer + VM)  │                      │  Store (skills.json) │   │
│   └────────────────────┘                      └──────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘

```

### 🧠 1. LLM-Driven Curriculum (`llama-3.1-8b-instant`)

* **Dynamic Subgoal Generation:** Evaluates inventory, nearby entities, time, biome, and spatial memory to propose the logical next task.
* **Curiosity & Stagnation Override:** Calculates a 0–10 Curiosity Score. If curiosity reaches $\ge 6$ or inventory progress stalls for 6 consecutive ticks, the agent overrides task planning to explore new chunks directionally.
* **Warm-Up Masking & Overflow Guards:** Masks non-essential items during early progression ($< 7$ tasks) to minimize token burn. If inventory usage hits $\ge 33/36$ slots, the agent automatically triggers a hardcoded chest placement and deposit routine.

### 🗺️ 2. Cartographer Spatial Memory (`world_map.py`)

* **Passive Chunk Logging:** The Node bridge scans a $7\times7\times7$ block volume around the bot every 8 blocks of movement and logs landmark blocks (ores, chests, spawners, portals, furnaces) to Python.
* **Underground Override:** Automatically overrides surface biome tags to `"underground"` when surrounding voxels lack surface blocks (dirt, grass, sand).
* **Distance-Sorted Telemetry:** Nearby entities are sorted strictly by ascending Euclidean distance (`nearest to farthest`).

### ⚡ 3. Dual-Representation Skill RAG

* **Docstring Embeddings:** Generated skills save both raw code and an LLM-summarized docstring `description`. Vector searches embed the natural language description rather than function names or raw code.
* **Augmented Failure Queries:** When retrying after a failure, the retrieval query is augmented with the error output and missing item logs (`query = context + "\n\nMissing: " + chat_log`).
* **Inline Pre-Injection:** Top-matched skills are pre-injected into code generation prompts as inline `async function` blocks, enabling functions like `await collectOakLog()` to be called directly without recompiling sandbox context.

### 🔬 4. Critic Agent (`llama-3.3-70b-versatile`)

* **Non-Blocking Failure Analysis:** Fires asynchronously upon execution failure or telemetry diff mismatch.
* **10-Point Diagnostic Checklist:** Evaluates stack traces, missing inventory items, wrong item IDs, unawaited promises, and pathfinding blocks.
* **Structured Self-Correction:** Outputs `failure_reason`, `suggested_fix`, and `avoid_patterns[]` to steer the next code generation attempt. Unrecoverable failures trigger an immediate short-circuit to save API quota.

### 🛡️ 5. Control Primitives & Hardened Sandbox

* **Direct API Prohibition:** The system prompt strictly forbids raw Mineflayer calls (`bot.dig`, `bot.craft`, `bot.openFurnace`, `bot.placeBlock`, `bot.attack`).
* **Mandatory Primitives:** All physical actions are enforced through high-level primitive wrappers: `mineBlock`, `craftItem`, `smeltItem`, `placeItem`, `killMob`, and `exploreUntil`.
* **VM Isolation:** Code executes inside Node's `vm` module with `Object.freeze()` prototype locking, isolated timeouts, and global `unhandledRejection` safety nets.

---

## 🚀 Getting Started

### Prerequisites

* **Node.js**: v18.x or higher
* **Python**: 3.9 or higher
* **Minecraft Server**: Local LAN world or dedicated server running v1.21.x (listening on `localhost:25565`)
* **Groq API Key**: Obtainable from [console.groq.com](https://console.groq.com)

---

### Installation & Setup

#### 1. Configure the Node.js Bridge

```bash
cd mc_bridge
npm install
node bot.js

```

*The bridge will connect to your local Minecraft instance and log:*

`[MC Bridge] Hardened server listening on http://localhost:3000`

#### 2. Configure the Python Brain

Open a new terminal session and navigate to the `brain` directory:

```bash
cd brain

# Create and activate a virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Export your Groq API key
# On Windows (PowerShell):
$env:GROQ_API_KEY="your_groq_api_key_here"
# On Linux/macOS:
export GROQ_API_KEY="your_groq_api_key_here"

# Start the FastAPI server
uvicorn main:app --reload

```

---

## 🎛️ STT Control Center

Once both servers are running, open your web browser and navigate to:

```text
http://localhost:8000

```

The Web Dashboard provides a real-time monitor into ARIES's cognitive state:

* **Speech-to-Text Dictation:** Dictate master goals using the Web Speech API interface.
* **LLM Rationale Card:** Displays real-time reasoning behind task proposals.
* **Curiosity Gauge:** Dynamic visual indicator showing current exploration drive (0–10 scale).
* **Cartographer POI Pills:** Color-coded count of discovered world landmarks and ores.
* **Critic Report Panel:** Displays natural-language root-cause diagnoses and suggested fixes whenever a script fails.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.