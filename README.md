# MC Aries Bot ♈

An autonomous, multi-modal Minecraft AI agent built with a dual-server architecture. It bridges a Python-based intelligence layer (powered by Groq Llama 3) with a Node.js execution engine (using Mineflayer), enabling true natural language control both in-game and via API.

## 🏗️ Architecture

The project splits capabilities into two distinct cooperating layers:

### 1. The Execution Bridge (Node.js / Mineflayer)
* **Tech Stack:** Node.js, Express, Mineflayer (`mineflayer-pathfinder`, `mineflayer-collectblock`, `mineflayer-pvp`).
* **Role:** Connects directly to the Minecraft server as an automated entity. It exposes low-level REST endpoints to execute in-game macros and listens to live chat triggers.
* **Port:** `3000`

### 2. The AI Brain (Python / FastAPI & Groq)
* **Tech Stack:** Python, FastAPI, Uvicorn, Groq SDK (`llama-3.3-70b-versatile`).
* **Role:** Acts as the cognitive core. Using native LLM tool-calling (function calling), it interprets natural language prompts and translates them into sequential sequences of agentic actions.
* **Port:** `8000`

---

## 🚀 Getting Started

### Prerequisites
* [Node.js](https://nodejs.org/) installed
* [Python 3.8+](https://www.python.org/) installed
* A running Minecraft server (Java Edition)
* A free [Groq API Key](https://console.groq.com/)

### 1. Start the Node.js Bridge
Open a terminal, navigate to the bridge directory, and run:
```bash
cd mc_bridge
npm install
node bot.js

```

### 2. Start the Python AI Brain

Open a second terminal, configure your environment, and spin up FastAPI:

```bash
cd brain
python -m venv venv
.\venv\Scripts\Activate  # Use source venv/bin/activate on macOS/Linux
pip install fastapi uvicorn requests groq python-dotenv

```

Create a `.env` file inside the `brain` folder and add your key:

```env
GROQ_API_KEY=your_groq_api_key_here

```

Run the server:

```bash
uvicorn main:app --reload

```

---

## 🕹️ Usage & Commands

### In-Game Natural Language (LLM Agent)

You can talk to Aries directly inside Minecraft chat using the **`aries`** or **`ai`** prefix. The AI will reason through tools, execute the workflow, and respond in-game:

* `aries check what is currently in your inventory`
* `aries collect 3 dirt and deposit it into the chest`
* `aries craft 4 oak planks`
* `aries fight the nearest zombie`

### Legacy / Direct Chat Commands

* `come` — Pathfinds to your exact coordinates.
* `collect [block_name] [count]` — Mines specific resources (e.g., `collect oak_log 5`).
* `fight [mob_name]` — Equips a weapon and engages target entities (e.g., `fight zombie`).
* `stop` — Instantly interrupts all actions, pathfinding, and combat.

### HTTP Endpoints (FastAPI Brain)

* `POST /agent/chat` — Send natural language JSON payload `{"prompt": "your command"}`.
* `POST /agent/move` — Send `{"x": 100, "y": 64, "z": -200}`.
* `POST /agent/collect` — Send `{"blockName": "stone", "count": 10}`.
* `POST /agent/craft` — Send `{"itemName": "stick", "count": 4}`.
* `POST /agent/deposit` — Send `{"itemName": "dirt", "count": 10}`.
* `POST /agent/fight` — Send `{"mobName": "skeleton"}`.
* `GET /agent/inventory` — Fetches current bot inventory contents.
* `DELETE /agent/stop` — Halts execution state.