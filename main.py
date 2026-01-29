import asyncio
import json
import os
import random
import time
from openai import AsyncOpenAI
import websockets
from websockets.exceptions import ConnectionClosed
from dotenv import load_dotenv

load_dotenv()

MAX_TOKEN = os.getenv("MAX_TOKEN")
MAX_WS_URI = os.getenv("MAX_WS_URI", "wss://ws-api.oneme.ru/websocket")
MAX_WS_ORIGIN = os.getenv("MAX_WS_ORIGIN", "https://web.max.ru")
RECONNECT_DELAY = 5

LLM_API_URL = os.getenv("LLM_API_URL", "http://127.0.0.1:8317/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "my-secret-key")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3-flash-preview")

client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_API_URL)

if not MAX_TOKEN:
    raise RuntimeError("set MAX_TOKEN in .env")

_seq = 100

def next_seq():
    global _seq
    _seq += 1
    return _seq

async def send_max_message(websocket, chat_id, text):
    seq = next_seq()
    cid = -int(time.time() * 1000) - random.randint(100, 999)
    
    request = {
        "ver": 11,
        "cmd": 0,
        "seq": seq,
        "opcode": 64,
        "payload": {
            "chatId": int(chat_id),
            "message": {
                "text": text,
                "cid": cid,
                "elements": [],
                "attaches": []
            },
            "notify": True
        }
    }
    
    try:
        await websocket.send(json.dumps(request))
        print(f"[SEND] Chat {chat_id}: {text}")
    except Exception as e:
        print(f"[ERROR] message failed: {e}")

async def get_llm_response(text):
    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": text}]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[ERROR] LLM: {e}")
        return None

async def handle_max_message(websocket, data):
    try:
        opcode = data.get("opcode")
        payload = data.get("payload", {})
        
        if opcode == 64:
            msg_data = payload.get("message", {})
            chat_id = msg_data.get("sender")
            text = msg_data.get("text", "")
            attaches = msg_data.get("attaches", [])
        elif opcode == 128:
            msg_data = payload.get("message", {})
            chat_id = payload.get("chatId")
            text = msg_data.get("text", "")
            attaches = msg_data.get("attaches", [])
        else:
            return

        if (not text and not attaches) or not chat_id:
            return

        if text:
            print(f"[RECV] Chat {chat_id}: {text}")

        if attaches:
            for a in attaches:
                if a.get("_type") == "PHOTO":
                    file_name = a.get("name")
                    if not file_name or file_name == "unnamed":
                        file_name = "picture"
                    file_id = a.get("fileId", "no-id")
                    print(f"[IMAGE] {chat_id}: {file_name} (ID: {file_id})")

        clean_text = text.lower().strip()
        
        if clean_text == "!ping":
            await send_max_message(websocket, chat_id, "pong")
        elif clean_text.startswith("!echo "):
            echo_val = text[6:]
            await send_max_message(websocket, chat_id, echo_val)
        elif text:
            print(f"[LLM] Requesting response for: {text[:50]}...")
            ai_response = await get_llm_response(text)
            if ai_response:
                await send_max_message(websocket, chat_id, ai_response)
            
    except Exception as e:
        print(f"[ERROR] message failed: {e}")

async def connect_to_max():
    while True:
        try:
            print(f"connection to {MAX_WS_URI}...")
            async with websockets.connect(
                MAX_WS_URI,
                origin=MAX_WS_ORIGIN,
                additional_headers={"User-Agent": "Mozilla/5.0"}
            ) as websocket:
                
                await websocket.send(json.dumps({
                    "ver": 11, "cmd": 0, "seq": next_seq(), "opcode": 6,
                    "payload": {
                        "userAgent": {
                            "deviceType": "WEB", "locale": "ru", "osVersion": "Linux",
                            "deviceName": "Firefox", "appVersion": "25.7.11"
                        },
                        "deviceId": "selfbot_client"
                    }
                }))
                await websocket.recv()

                await websocket.send(json.dumps({
                    "ver": 11, "cmd": 0, "seq": next_seq(), "opcode": 19,
                    "payload": {
                        "token": MAX_TOKEN,
                        "chatsSync": 0, "contactsSync": 0
                    }
                }))

                print("started")

                while True:
                    message = await websocket.recv()
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    
                    if not isinstance(data, dict):
                        continue

                    opcode = data.get("opcode")
                    if opcode in [64, 128]:
                        asyncio.create_task(handle_max_message(websocket, data))

        except ConnectionClosed:
            print(f"connection closed. retry in {RECONNECT_DELAY}s...")
        except Exception as e:
            print(f"err: {e}. retry in {RECONNECT_DELAY}s...")
        
        await asyncio.sleep(RECONNECT_DELAY)

async def main():
    await connect_to_max()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstop")
