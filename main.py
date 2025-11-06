# main.py (relevant bits)
from user_input_manager import UserInputManager
from llm_agent import get_next_action
from browser_agent import BrowserAgent
from dataset_manager import DatasetManager
import os, time
from dotenv import load_dotenv
load_dotenv()
from collections import defaultdict, deque

recent_fills = defaultdict(int)
recent_actions = deque(maxlen=8)

def looks_like_auth_screen(visible_text: str) -> bool:
    s = (visible_text or "").lower()
    keywords = [
        "log in", "login", "sign in", "sign-in", "continue with email",
        "verification code", "one-time code", "otp", "enter code", "magic link",
        "back to login", "continue with login code"
    ]
    return any(k in s for k in keywords)

def goal_completed_guard(user_task: str, visible_text: str) -> bool:
    """
    Super simple generic guard: block 'done' if still in auth flows.
    You can enrich this per app later (e.g., success banners).
    """
    if looks_like_auth_screen(visible_text):
        return False
    return True


def run_agent(app_url, app_name, user_task):
    browser = BrowserAgent(headless=False)
    inputs = UserInputManager()
    data = DatasetManager()

  
    task_dir = data.create_task_dir(app_name, user_task)
    metadata = {
        "task_title": user_task.splitlines()[0][:120],  # short header
        "task_full": user_task,                         # entire prompt
        "steps": []
    }


    browser.navigate(app_url)
    try:
        browser.page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    step = 1
    prev_result = None
    fail_streak = 0
    latest_screenshot_path = None

    while step <= 40:  # safety cap
        visible = browser.get_visible_text()
        action = get_next_action(user_task, visible, prev_result, latest_screenshot_path)
        print(f"LLM action: {action}")

        if action.get("action") == "fill":
            key = (action.get("_normalized_selector") or action.get("selector") or "").strip()
            val_sig = (action.get("value") or "")[:24]  # short signature
            recent_actions.append(("fill", key, val_sig))
            recent_fills[key] += 1
            if recent_fills[key] >= 3:
                prev_result = f"Guard: selector '{key}' used repeatedly; propose a more specific selector (e.g., aria-label/role/name) to avoid wrong field."
                recent_fills[key] = 0
                continue

        def looks_like_auth_screen(visible_text: str) -> bool:
            s = (visible_text or "").lower()
            keywords = [
                "log in", "login", "sign in", "sign-in",
                "continue with email",
                "verification code", "one-time code", "otp", "enter code", "magic link",
                "back to login", "continue with login code"
            ]
            return any(k in s for k in keywords)

        if action.get("action") == "done":
            has_real_input_meta = bool(
                (action.get("field")) or
                (action.get("prompt")) or
                (action.get("selector") and action.get("selector").strip())
            )
            if looks_like_auth_screen(visible) or has_real_input_meta:
                field = (action.get("field") or
                        ("email" if "email" in visible.lower() else
                        ("otp" if any(w in visible.lower() for w in ["code","otp","verification"]) else "custom")))
                sel = action.get("selector") or (
                    'input[type="email"]' if field == "email" else
                    ('input[autocomplete="one-time-code"], input[name*="code"], input[placeholder*="code" i]'
                    if field in ("otp","code") else "")
                )

                if field == "custom" and not sel:
                    prev_result = "Ignoring spurious input request on a non-auth screen."
                    action = {"action": "done"}  
                else:
                    action = {
                        "action": "request_input",
                        "selector": sel,
                        "value": "",
                        "take_screenshot": True,
                        "screenshot_description": f"Awaiting {field}",
                        "field": field,
                        "prompt": action.get("prompt") or f"Enter your {field}",
                        "mask": action.get("mask", field in ("password","otp","code")),
                        "persist_key": action.get("persist_key") or f"auth.{field}",
                        "_selector_engine": action.get("_selector_engine"),
                        "_normalized_selector": action.get("_normalized_selector") or sel,
                        "_get_by_arg": action.get("_get_by_arg"),
                    }
            else:
                print("✅ Task marked complete.")
                break


        if action.get("action") == "request_input":
            if not action.get("selector"):
                prev_result = "Error: request_input missing selector; please return CSS selector for the input field."
                continue

            field = (action.get("field") or "custom").lower()
            prompt = action.get("prompt") or f"Enter {field}"
            mask = bool(action.get("mask", field in ("password", "otp", "code")))
            persist_key = action.get("persist_key")

            user_value = inputs.request(field, prompt, mask, persist_key)

            followup = {
                "action": "fill",
                "selector": action.get("selector", ""),  # must be provided by LLM
                "value": user_value,
                "take_screenshot": True,
                "screenshot_description": f"Filled {field}",
                "_selector_engine": action.get("_selector_engine"),
                "_normalized_selector": action.get("_normalized_selector"),
                "_get_by_arg": action.get("_get_by_arg"),
            }
            keep_going = browser.execute_action(followup)

            if followup.get("take_screenshot"):
                img_path = os.path.join(task_dir, f"step_{step}.png")
                browser.screenshot(img_path)
                metadata["steps"].append({
                    "step": step,
                    "desc": followup.get("screenshot_description", ""),
                    "image": os.path.basename(img_path),
                })
                latest_screenshot_path = img_path
                step += 1

            prev_result = browser.last_result
            fail_streak = 0
            print(prev_result)
            time.sleep(0.5)
            continue  


        keep_going = browser.execute_action(action)

        if action.get("take_screenshot"):
            img_path = os.path.join(task_dir, f"step_{step}.png")
            browser.screenshot(img_path)
            metadata["steps"].append({
                "step": step,
                "desc": action.get("screenshot_description", ""),
                "image": os.path.basename(img_path),
            })
            latest_screenshot_path = img_path

        if "Error" in browser.last_result or "Timeout" in browser.last_result:
            fail_streak += 1
        else:
            fail_streak = 0

        print(f"Step {step}: {browser.last_result}")

        if not keep_going or fail_streak >= 3:
            print("Stopping due to completion or repeated failures.")
            break

        prev_result = browser.last_result
        step += 1
        time.sleep(1.0)

    data.save_metadata(task_dir, metadata)
    browser.close()
    print(f"📸 Captured {len(metadata['steps'])} screenshots at: {task_dir}")






# if __name__ == "__main__":
#     run_agent(
#         app_url="https://linear.app/",
#         app_name="linear",
#         user_task="""Create a new project in Linear with the following details, then land on the project's page:

# - Name: "Apollo Launch"
# - Description: "End-to-end agent demo: capture UI states for project creation and basic setup."
# - Status: In Progress
# - Priority: High  (if project priority isn’t available for Projects, add a label/tag "High Priority")


# Notes:
# - Prefer the built-in fields first; if a field isn’t available in the Project form, use the closest alternative (e.g., label/tag).
# - Capture screenshots at each meaningful state (open create form, filled form, created/success, final project page)."""
#     )




# if __name__ == "__main__":
#     run_agent(
#         app_url="https://app.asana.com/",
#         app_name="Asana",
#         user_task="""Create a new task named: Create Apollo Launch

# Context & behavior hints:
# - Work from the main task list (e.g., “My tasks” or the current project). Use a clear entry point such as role=button[name=/Add task|New task|Create task/i] or a visible inline “Add task” row.
# - The title field is usually a single-line input or a contenteditable area. Focus it, replace any placeholder, type: Create Apollo Launch, and press Enter to save.
# - Prefer ARIA/role selectors over bare text. For ambiguous labels, scope to the visible panel or dialog (e.g., role=dialog[...] >> ...).
# - During sign-in, there may be multiple “Continue” actions (Google/Microsoft providers). Choose the “Continue” associated with the email/password/OTP flow (e.g., role=button[name=/Continue/i] near the email or code field), not SSO provider buttons.

# Success / stop conditions (very important):
# - Stop immediately after the task is added (i.e., you pressed Enter and the new task row/card shows “Create Apollo Launch”), OR
# - Stop if you can already see a task with the exact name “Create Apollo Launch” in the current list before adding it (avoid duplicates), OR
# - Stop right after clicking an “Add task”/inline row, typing the name, and confirming (Enter) if the UI indicates the task has been created.

# Screenshots:
# - Capture at meaningful points: opening the list, the “add task” UI focused, after the task is created and visible."""
#     )




if __name__ == "__main__":
    run_agent(
        app_url="https://linear.app/",
        app_name="linear",
        user_task="""Post the first project update on the existing Linear project, then land back on the project's page:

- Project: "Apollo Launch"
- Update text: "Created an AI agent to navigate web apps."

Flow requirements (do these in order):
1) Open the Projects section (from the sidebar or global nav).
2) Click the project named "Apollo Launch" to open its project page.
3) Once the project page "Apollo Launch" opens, locate the Updates/Write your first update button
   - If you see a call-to-action such as "Write an update", "Write your first update", or a button labeled "Update", click it.
   - Otherwise open the “Updates” or “Activity” tab/pane if it exists.
4) Write a new update with exactly: Created an AI agent to navigate web apps.
5) Publish/post the update.
6) Verify the update appears at the top of the project's updates/activity feed.

Stopping condition:
- If, upon opening the "Apollo Launch" project page (before attempting to write), the latest visible update already matches exactly:
  Created an AI agent to navigate web apps
  then stop and mark the task complete (do not post a duplicate).

Selector & behavior guidelines:
- Scope all selectors to the project view once it’s open (avoid clicking global nav accidentally).
- Prefer ARIA/role selectors (e.g., role=button[name=/Write an update|Update|Post update/i]) over bare text.
- If the editor is contenteditable, focus it, select-all, and replace with the update text verbatim.
- Idempotency: do not post if the most recent update already matches the requested text.

Notes:
- Capture screenshots at key states: project list, project page loaded, editor opened, update posted, final project page."""
    )
