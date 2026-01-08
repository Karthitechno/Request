from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import json
import asyncio
import req
from typing import List

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory session store: session_id -> {status, result, error, otp_event}
SESSION_STORE = {}


@app.post("/start")
async def start(credentials: str = Form(None), excel: UploadFile = File(None)):
    # credentials: JSON string representing list of {username,password}
    try:
        creds_list = json.loads(credentials) if credentials else []
    except Exception:
        return JSONResponse({"error": "Invalid credentials JSON"}, status_code=400)

    excel_bytes = None
    if excel is not None:
        excel_bytes = await excel.read()

    session_id = req.create_session_id()
    # initialize top-level session entry
    SESSION_STORE[session_id] = {"status": "scheduled", "children": [], "result": None, "error": None}
    # schedule background task that will create per-credential child sessions
    asyncio.create_task(req.start_scheduled_sessions(session_id, creds_list, excel_bytes, SESSION_STORE))

    return {"session_id": session_id, "message": "Session scheduled. Browsers will open for each credential; press Continue after completing OTP in each browser."}


@app.post("/continue/{session_id}")
async def continue_after_otp(session_id: str):
    s = SESSION_STORE.get(session_id)
    if not s:
        return JSONResponse({"error": "Unknown session_id"}, status_code=404)
    # set otp_event for all child sessions under this top-level session
    continued = 0
    # if top-level has an otp_event (backwards-compat), set it
    evt = s.get("otp_event")
    if evt and not evt.is_set():
        evt.set()
        continued += 1

    # set events for children
    for child_id in s.get("children", []):
        child = SESSION_STORE.get(child_id)
        if child and child.get("otp_event") and not child["otp_event"].is_set():
            child["otp_event"].set()
            continued += 1

    return {"status": "continuing", "continued_children": continued}


@app.get("/status/{session_id}")
async def status(session_id: str):
    s = SESSION_STORE.get(session_id)
    if not s:
        return JSONResponse({"error": "Unknown session_id"}, status_code=404)
    # aggregate children statuses
    children = {}
    for child_id in s.get("children", []):
        c = SESSION_STORE.get(child_id)
        if c:
            children[child_id] = {"status": c.get("status"), "result": c.get("result"), "error": c.get("error"), "next_run": c.get("next_run")}

    return {"status": s.get("status"), "result": s.get("result"), "error": s.get("error"), "children": children}


@app.get("/")
async def index():
    return FileResponse('static/index.html')


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
