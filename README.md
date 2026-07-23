# 🌌 ARIES // Autonomous Minecraft Agent

![Node.js](https://img.shields.io/badge/Node.js-339933?style=for-the-badge&logo=nodedotjs&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Tailwind](https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-F55036?style=for-the-badge&logo=groq&logoColor=white)

ARIES (Autonomous Reactive Intelligent Embodied System) is a dual-server cognitive architecture for Minecraft, heavily inspired by the [MineDojo Voyager](https://github.com/MineDojo/Voyager) framework. 

It pairs a high-performance **Node.js/Mineflayer bridge** with an asynchronous **Python/FastAPI brain** powered by Groq LLMs. ARIES dynamically writes its own JavaScript on the fly, tests it in an isolated VM sandbox, mathematically verifies the results, and saves successful routines to a vector memory library for future reuse.

---

## ⚡ Core Architecture

### 🧠 The Cognitive Brain (FastAPI)
- **Asynchronous Execution Loop:** Built with `httpx` and `asyncio.Lock()` to handle high-frequency telemetry polling and script execution without blocking the event loop.
- **Mathematical Telemetry Diffing:** Verifies sub-goal success strictly via mathematical state changes (e.g., checking `post_inventory - pre_inventory >= target`).
- **Vector Skill Library:** Automatically caches and retrieves successful, dynamically generated LLM code snippets (supporting ChromaDB and JSON fallback).
- **Curriculum Planner:** A background dependency-tree evaluator that automatically dictates the agent's next sub-goal based on health, biome, and inventory state.

### 🛡️ The Hardened Bridge (Node.js)
- **Bulletproof VM Sandbox:** Executes LLM-generated code in a strictly controlled `vm` context. `Object.freeze()` prevents prototype pollution, and all async `setTimeout` handles are tracked and destroyed post-execution.
- **Crash-Proof Safety Nets:** Global `unhandledRejection` guards and isolated `.catch()` chains ensure floating LLM promises never crash the bridge server.
- **Resilient Crafting Engine:** Features an item alias dictionary to normalize LLM hallucinations (e.g., `wooden_plank` -> `oak_planks`), fuzzy-matching, and automatic pathfinding to 3x3 crafting tables.

### 🎛️ STT Control Center (Alpine.js + Tailwind)
- **Zero-Latency Dictation:** Features browser-native Web Speech API integration for instant voice-to-text commands.
- **High-Polish UI/UX:** A modern, dark-mode dashboard featuring mesh gradients, glassmorphism, and live telemetry tracking (Health, Food, XYZ, Biome, and Inventory).
- **Dynamic Goal Editing:** Instantly pivot the agent's master goal via the realtime REST API.

---

## 🚀 Getting Started

### Prerequisites
- Node.js (v18+)
- Python (3.9+)
- A Minecraft Server (v1.21.x recommended)
- A Groq API Key

### 1. Setup the Node.js Bridge
Navigate to the `mc_bridge` directory and install the dependencies.
```bash
cd mc_bridge
npm install
node bot.js

```

*Note: Ensure your local Minecraft server or LAN world is open to `localhost:25565`.*

### 2. Setup the Python Brain

Navigate to the `brain` directory, set up your environment, and launch the API.

```bash
cd brain
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
pip install -r requirements.txt

# Add your Groq API key to a .env file
echo "GROQ_API_KEY=your_key_here" > .env

uvicorn main:app --reload

```

### 3. Launch the Dashboard

Open your browser and navigate to `http://localhost:8000` to access the ARIES Control Center. Click the microphone icon to begin dictating commands, or let the background Curriculum Planner run autonomously!

---

## 🔒 Security & Sandboxing Note

ARIES dynamically executes untrusted LLM code. The `/api/execute-script` endpoint is hardened against infinite loops, coordinate injection, and prototype pollution, but this bridge should **never** be exposed directly to the public internet without strict authentication middleware.

## 📄 License

MIT License

