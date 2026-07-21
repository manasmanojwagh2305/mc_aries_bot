# MC Aries Bot ♈

An agentic Minecraft bot built with a dual-server architecture, bridging a Python-based AI intelligence gateway with a Node.js Minecraft execution engine.

## 🏗️ Architecture

This project is split into two distinct layers that communicate via REST APIs:

### 1. The Execution Bridge (Node.js / Mineflayer)
The lower-level engine responsible for direct interaction with the Minecraft server. 
* **Tech Stack:** Node.js, Express, Mineflayer (mineflayer-pathfinder, mineflayer-collectblock, mineflayer-pvp).
* **Role:** Connects to the Minecraft server as a player entity. It listens for incoming HTTP requests and executes in-game macros (moving, mining, placing blocks, fighting). It also listens to in-game chat events and relays state back to the brain.
* **Port:** `3000`

### 2. The AI Brain (Python / FastAPI)
The high-level intelligence and routing layer.
* **Tech Stack:** Python, FastAPI, Uvicorn.
* **Role:** Acts as the central gateway. It exposes clean API endpoints (e.g., `/agent/move`, `/agent/collect`) that can be consumed by a web frontend or an LLM loop. When a command is received, the brain parses the intent and forwards the exact coordinates or block data to the Node.js bridge.
* **Port:** `8000`

---

## 🚀 Getting Started

To run the bot locally, you must spin up both the Node.js bridge and the Python brain.

### Prerequisites
* [Node.js](https://nodejs.org/) installed
* [Python 3.8+](https://www.python.org/) installed
* A running Minecraft server (Java Edition)

### 1. Start the Node.js Bridge
Open a terminal, navigate to the bridge directory, and run:
```bash
cd mc_bridge
npm install
node bot.js
```
_The bot should now join your Minecraft server._
### 2. Start the Python Brain
Open a second terminal, navigate to the brain directory, and run:
```bash
cd brain
pip install fastapi uvicorn requests
uvicorn main:app --reload
```
_The FastAPI server is now running at_ `http://localhost:8000`

## 🕹️ API Usage & Commands
You can interact with the bot using HTTP requests to the Python brain, or by typing directly in the in-game chat.
### In-Game Chat Commands
- `come` - The bot will pathfind to your current location.
- `collect [block_name] [count]` - The bot will search for and gather the specified blocks (e.g., `collect oak_log 5`).
- `stop` - Halts all current pathfinding or collection tasks instantly.

### HTTP Endpoints (FastAPI)
- `POST /agent/move` - Send `{"x": 100, "y": 64, "z": -200}` to trigger pathfinding.
- `POST /agent/collect` - Send `{"blockName": "dirt", "count": 10}` to gather items.
- `POST /agent/place` - Send `{"blockName": "cobblestone", "x": 0, "y": 64, "z": 0}` to place blocks.
- `GET /agent/inventory` - Returns the bot's current held items.
- `DELETE /agent/stop` - Interrupts current actions.
