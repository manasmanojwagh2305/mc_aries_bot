import os
import json
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

app = FastAPI(title="ARIES Brain-Bridge Gateway")

# Initialize Groq client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Node.js Minecraft bridge URL
MC_BRIDGE_URL = "http://localhost:3000/api"

# --- PYDANTIC MODELS ---
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
    count: int = None # If null, deposits all of that item

class CraftRequest(BaseModel):
    itemName: str
    count: int = 1

class FightRequest(BaseModel):
    mobName: str

class NaturalLanguagePrompt(BaseModel):
    prompt: str


# --- EXISTING DIRECT API ENDPOINTS ---

@app.post("/agent/move")
def command_move(req: MoveRequest):
    try:
        response = requests.post(f"{MC_BRIDGE_URL}/move", json=req.dict())
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agent/collect")
def command_collect(req: CollectRequest):
    try:
        response = requests.post(f"{MC_BRIDGE_URL}/collect", json=req.dict())
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/agent/stop")
def command_stop():
    try:
        response = requests.delete(f"{MC_BRIDGE_URL}/stop")
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/agent/inventory")
def get_inventory():
    try:
        response = requests.get(f"{MC_BRIDGE_URL}/inventory")
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agent/place")
def command_place(req: PlaceRequest):
    try:
        response = requests.post(f"{MC_BRIDGE_URL}/place", json=req.dict())
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agent/pvp")
def command_pvp():
    try:
        response = requests.post(f"{MC_BRIDGE_URL}/pvp")
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agent/deposit")
def command_deposit(req: DepositRequest):
    try:
        response = requests.post(f"{MC_BRIDGE_URL}/deposit", json=req.dict())
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agent/craft")
def command_craft(req: CraftRequest):
    try:
        response = requests.post(f"{MC_BRIDGE_URL}/craft", json=req.dict())
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agent/fight")
def command_fight(req: FightRequest):
    try:
        response = requests.post(f"{MC_BRIDGE_URL}/fight", json=req.dict())
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- LLM TOOL DEFINITIONS ---

def tool_move(x: float, y: float, z: float):
    res = requests.post(f"{MC_BRIDGE_URL}/move", json={"x": x, "y": y, "z": z})
    return res.json()

def tool_collect(blockName: str, count: int = 1):
    res = requests.post(f"{MC_BRIDGE_URL}/collect", json={"blockName": blockName, "count": count})
    return res.json()

def tool_place(blockName: str, x: float, y: float, z: float):
    res = requests.post(f"{MC_BRIDGE_URL}/place", json={"blockName": blockName, "x": x, "y": y, "z": z})
    return res.json()

def tool_deposit(itemName: str, count: int = None):
    res = requests.post(f"{MC_BRIDGE_URL}/deposit", json={"itemName": itemName, "count": count})
    return res.json()

def tool_craft(itemName: str, count: int = 1):
    res = requests.post(f"{MC_BRIDGE_URL}/craft", json={"itemName": itemName, "count": count})
    return res.json()

def tool_fight(mobName: str):
    res = requests.post(f"{MC_BRIDGE_URL}/fight", json={"mobName": mobName})
    return res.json()

def tool_pvp():
    res = requests.post(f"{MC_BRIDGE_URL}/pvp")
    return res.json()

def tool_get_inventory():
    res = requests.get(f"{MC_BRIDGE_URL}/inventory")
    return res.json()

def tool_stop():
    res = requests.delete(f"{MC_BRIDGE_URL}/stop")
    return res.json()

AVAILABLE_TOOLS = {
    "tool_move": tool_move,
    "tool_collect": tool_collect,
    "tool_place": tool_place,
    "tool_deposit": tool_deposit,
    "tool_craft": tool_craft,
    "tool_fight": tool_fight,
    "tool_pvp": tool_pvp,
    "tool_get_inventory": tool_get_inventory,
    "tool_stop": tool_stop,
}

GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tool_move",
            "description": "Move the bot to specific X, Y, Z coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"}
                },
                "required": ["x", "y", "z"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_collect",
            "description": "Mine or collect blocks in the world like oak_log, dirt, stone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "blockName": {"type": "string"},
                    "count": {"type": "integer"}
                },
                "required": ["blockName"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_place",
            "description": "Place a block at specific X, Y, Z coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "blockName": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"}
                },
                "required": ["blockName", "x", "y", "z"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_deposit",
            "description": "Deposit items into a nearby chest.",
            "parameters": {
                "type": "object",
                "properties": {
                    "itemName": {"type": "string"},
                    "count": {"type": "integer"}
                },
                "required": ["itemName"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_craft",
            "description": "Craft items from inventory materials like oak_planks or sticks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "itemName": {"type": "string"},
                    "count": {"type": "integer"}
                },
                "required": ["itemName"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_fight",
            "description": "Fight a specific hostile mob such as a zombie or skeleton.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mobName": {"type": "string"}
                },
                "required": ["mobName"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_pvp",
            "description": "Engage the nearest hostile mob automatically."
            # parameters key removed
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_get_inventory",
            "description": "Check the bot's current inventory contents."
            # parameters key removed
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_stop",
            "description": "Stop all current actions immediately."
            # parameters key removed
        }
    }
]



# --- THE LLM AGENT ENDPOINT ---

@app.post("/agent/chat")
def agent_chat(req: NaturalLanguagePrompt):
    """Send natural language instructions to Aries, powered by Groq LLM tool calling."""
    try:
        messages = [
            {
                "role": "system",
                "content": "You are Aries, an autonomous AI Minecraft agent. Use the provided tools to fulfill the user's instructions accurately. If asked to do multiple things, use the tools in the correct logical sequence."
            },
            {
                "role": "user",
                "content": req.prompt
            }
        ]

        # First LLM call: see if it wants to invoke a tool
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=GROQ_TOOLS,
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if not tool_calls:
            return {"status": "success", "response": response_message.content, "tools_executed": []}

        # Handle tool execution
        executed_tools = []
        messages.append(response_message)

        for tool_call in tool_calls:
            function_name = tool_call.function.name
            raw_args = tool_call.function.arguments
            function_args = json.loads(raw_args) if raw_args else {}
            if not isinstance(function_args, dict):
                function_args = {}

            if function_name in AVAILABLE_TOOLS:
                tool_function = AVAILABLE_TOOLS[function_name]
                tool_result = tool_function(**function_args)
                executed_tools.append(function_name)
                
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": json.dumps(tool_result)
                })

        # Second LLM call: get the final natural language summary after tool execution
        second_response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages
        )

        return {
            "status": "success",
            "response": second_response.choices[0].message.content,
            "tools_executed": executed_tools
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))