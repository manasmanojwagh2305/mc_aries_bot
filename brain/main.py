import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="ARIES Brain-Bridge Gateway")

# Node.js Minecraft bridge URL
MC_BRIDGE_URL = "http://localhost:3000/api"

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