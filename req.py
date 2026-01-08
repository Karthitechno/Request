import asyncio
import datetime
import pandas as pd
import os
import io
import uuid
from typing import List, Dict, Optional
from playwright.async_api import async_playwright

# ------------------ CONFIG ------------------
LOGIN_URL = "https://aptitudeguruhem.com/AdminLogin"
REQUESTS_URL = "https://aptitudeguruhem.com/list-of-student-request"


def read_excel_users_from_bytes(excel_bytes: bytes) -> Optional[pd.DataFrame]:
    if not excel_bytes:
        return None
    try:
        df = pd.read_excel(io.BytesIO(excel_bytes))
        df['Name'] = df['Name'].astype(str).str.strip()
        df['Email'] = df['Email'].astype(str).str.strip().str.lower()
        return df
    except Exception:
        return None


async def go_to_requests(page):
    await page.click('a[href="/list-of-student-request"], a:has-text("Requests")')
    await page.wait_for_load_state("networkidle")


async def scrape_requests(page):
    try:
        await page.wait_for_selector('button.sc-hkaVUD.iFRrOl', timeout=15000)
    except Exception:
        return []

    requests = []
    rows = page.locator('div, tr, li, .request-card, .request-item').filter(
        has=page.locator('button.sc-hkaVUD.iFRrOl')
    )
    count = await rows.count()

    for i in range(count):
        row = rows.nth(i)
        try:
            text_content = await row.inner_text()
            lines = [line.strip() for line in text_content.split('\n') if line.strip()]

            name = ""
            email = ""
            for line in lines:
                if "@" in line:
                    email = line.strip().lower()
                elif len(line.split()) >= 1 and not line.startswith("20"):
                    # loose heuristic
                    if any(c.isalpha() for c in line):
                        name = line.strip()

            requests.append({"name": name, "email": email, "row": row})
        except Exception:
            continue

    return requests


async def accept_matching_requests(page, excel_df, web_requests):
    accepted = 0
    for _, student in excel_df.iterrows():
        target_name = str(student.get('Name', '')).lower()
        target_email = str(student.get('Email', '')).lower()

        for req in web_requests:
            req_name = (req.get("name") or "").lower()
            req_email = (req.get("email") or "").lower()

            if (target_email and target_email in req_email) or (
                target_name and (target_name in req_name or any(word in req_name for word in target_name.split()))
            ):
                try:
                    accept_btn = req["row"].locator('button.sc-hkaVUD.iFRrOl')
                    await accept_btn.scroll_into_view_if_needed()
                    await accept_btn.click()
                    await asyncio.sleep(0.8)
                    accepted += 1
                except Exception:
                    continue
                break

    return accepted


async def accept_all_requests(page, web_requests):
    accepted = 0
    for req in web_requests:
        try:
            accept_btn = req["row"].locator('button.sc-hkaVUD.iFRrOl')
            await accept_btn.scroll_into_view_if_needed()
            await accept_btn.click()
            await asyncio.sleep(0.8)
            accepted += 1
        except Exception:
            continue
    return accepted


async def start_session(session_id: str, credentials: List[Dict[str, str]], excel_bytes: Optional[bytes], session_store: dict):
    """Run a background session. session_store is a dict where this function updates status/result and waits for an Event named 'otp_event'."""
    session_store[session_id] = {"status": "starting", "result": None, "error": None, "otp_event": asyncio.Event()}

    try:
        excel_df = read_excel_users_from_bytes(excel_bytes) if excel_bytes else None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()

            # Use first credential by default for login UI, but store all for potential iteration
            cred = credentials[0] if credentials else {}
            email = cred.get('username', '')
            password = cred.get('password', '')

            await page.goto(LOGIN_URL, wait_until="networkidle")
            if email:
                try:
                    await page.fill('input[name="email"], input[placeholder*="Email"]', email)
                except Exception:
                    pass
            if password:
                try:
                    await page.fill('input[name="password"], input[type="password"]', password)
                except Exception:
                    pass
            try:
                await page.click('button:has-text("Login"), button[type="submit"]')
            except Exception:
                pass

            # Pause and wait for OTP manual completion by the user in the opened browser
            session_store[session_id]["status"] = "waiting_for_otp"
            # Wait until external signal triggers continuation
            await session_store[session_id]["otp_event"].wait()

            # After OTP completed and user signaled, wait for navigation and proceed
            await page.wait_for_load_state("networkidle")
            session_store[session_id]["status"] = "navigating_to_requests"
            await go_to_requests(page)

            session_store[session_id]["status"] = "scraping_requests"
            web_requests = await scrape_requests(page)

            if not web_requests:
                session_store[session_id]["status"] = "no_requests_found"
                await browser.close()
                session_store[session_id]["result"] = {"accepted": 0}
                return

            if excel_df is not None and not excel_df.empty:
                session_store[session_id]["status"] = "accepting_matches"
                accepted_count = await accept_matching_requests(page, excel_df, web_requests)
            else:
                session_store[session_id]["status"] = "accepting_all"
                accepted_count = await accept_all_requests(page, web_requests)

            session_store[session_id]["status"] = "done"
            session_store[session_id]["result"] = {"accepted": accepted_count}
            await browser.close()

    except Exception as e:
        session_store[session_id]["status"] = "error"
        session_store[session_id]["error"] = str(e)


async def start_scheduled_sessions(session_id: str, credentials: List[Dict[str, str]], excel_bytes: Optional[bytes], session_store: dict, interval_minutes: int = 30, duration_hours: int = 24):
    """Create one child session per credential and run each on a schedule (every `interval_minutes` up to `duration_hours`)."""
    # prepare excel once
    excel_df = read_excel_users_from_bytes(excel_bytes) if excel_bytes else None

    child_tasks = []
    for i, cred in enumerate(credentials if credentials else [{}]):
        child_id = f"{session_id}:{i}"
        # register child
        session_store[session_id].setdefault("children", []).append(child_id)
        # record next_run as now (will be updated before each sleep)
        session_store[child_id] = {"status": "scheduled", "result": None, "error": None, "otp_event": asyncio.Event(), "next_run": None}
        task = asyncio.create_task(run_credential_loop(child_id, cred, excel_df, session_store, interval_minutes, duration_hours))
        child_tasks.append(task)

    # monitor children and update top-level status when done
    asyncio.create_task(monitor_children(session_id, child_tasks, session_store))


async def monitor_children(session_id: str, tasks: List[asyncio.Task], session_store: dict):
    try:
        session_store[session_id]["status"] = "running_children"
        await asyncio.gather(*tasks, return_exceptions=True)
        session_store[session_id]["status"] = "completed"
        # optionally aggregate results
        results = {}
        for child in session_store[session_id].get("children", []):
            c = session_store.get(child)
            if c:
                results[child] = {"status": c.get("status"), "result": c.get("result"), "error": c.get("error")}
        session_store[session_id]["result"] = results
    except Exception as e:
        session_store[session_id]["status"] = "error"
        session_store[session_id]["error"] = str(e)


async def run_credential_loop(child_id: str, credential: Dict[str, str], excel_df: Optional[pd.DataFrame], session_store: dict, interval_minutes: int, duration_hours: int):
    """Run automation repeatedly for a single credential until duration_hours have elapsed."""
    end_time = asyncio.get_event_loop().time() + duration_hours * 3600
    run_index = 0
    while asyncio.get_event_loop().time() < end_time:
        run_index += 1
        session_store[child_id]["status"] = f"starting_run_{run_index}"
        # set next_run to now for UI visibility
        try:
            session_store[child_id]["next_run"] = datetime.datetime.utcnow().isoformat() + 'Z'
        except Exception:
            pass
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False)
                page = await browser.new_page()

                email = credential.get('username', '')
                password = credential.get('password', '')

                await page.goto(LOGIN_URL, wait_until="networkidle")
                if email:
                    try:
                        await page.fill('input[name="email"], input[placeholder*="Email"]', email)
                    except Exception:
                        pass
                if password:
                    try:
                        await page.fill('input[name="password"], input[type="password"]', password)
                    except Exception:
                        pass
                try:
                    await page.click('button:has-text("Login"), button[type="submit"]')
                except Exception:
                    pass

                # Wait briefly to see if an OTP is required. If required, user can press Continue which sets the event.
                session_store[child_id]["status"] = "waiting_for_otp"
                try:
                    # wait for user to click Continue (external set) but don't block forever
                    await asyncio.wait_for(session_store[child_id]["otp_event"].wait(), timeout=120)
                except asyncio.TimeoutError:
                    # no OTP continuation received within timeout, proceed
                    pass

                await page.wait_for_load_state("networkidle")
                session_store[child_id]["status"] = "navigating_to_requests"
                await go_to_requests(page)

                session_store[child_id]["status"] = "scraping_requests"
                web_requests = await scrape_requests(page)

                if not web_requests:
                    session_store[child_id]["status"] = "no_requests_found"
                    await browser.close()
                    session_store[child_id]["result"] = {"accepted": 0}
                else:
                    if excel_df is not None and not excel_df.empty:
                        session_store[child_id]["status"] = "accepting_matches"
                        accepted_count = await accept_matching_requests(page, excel_df, web_requests)
                    else:
                        session_store[child_id]["status"] = "accepting_all"
                        accepted_count = await accept_all_requests(page, web_requests)

                    session_store[child_id]["status"] = "done_run"
                    session_store[child_id]["result"] = {"accepted": accepted_count}
                    await browser.close()

        except Exception as e:
            session_store[child_id]["status"] = "error"
            session_store[child_id]["error"] = str(e)

        # reset the otp_event for the next run (so the user can continue again if needed)
        try:
            session_store[child_id]["otp_event"] = asyncio.Event()
        except Exception:
            pass

        # compute and store next run time
        if asyncio.get_event_loop().time() + interval_minutes * 60 >= end_time:
            # no further runs
            session_store[child_id]["next_run"] = None
            break
        next_run_ts = datetime.datetime.utcnow() + datetime.timedelta(minutes=interval_minutes)
        try:
            session_store[child_id]["next_run"] = next_run_ts.isoformat() + 'Z'
        except Exception:
            pass
        await asyncio.sleep(interval_minutes * 60)

    # mark child finished
    if session_store.get(child_id, {}).get("status") != "error":
        session_store[child_id]["status"] = "finished"


def create_session_id() -> str:
    return str(uuid.uuid4())


if __name__ == "__main__":
    print("This module provides `start_session` for integration with a web UI.")
#mv5079677@gmail.com	- Sjit$123
#sjceagh@gmail.com - Sjce321@

