# llm_agent.py
import os
import json
import re
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
from utils_llm import image_to_data_url 

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ALLOWED_ACTIONS = {"click", "fill", "press", "navigate", "done"}


import re

CSS_START_TOKENS = (".", "#", "[", ":", "/", "(")  # include xpath/others if you want
CSS_TAG_PREFIXES = ("input", "button", "a", "form", "label", "textarea", "select", "div", "span")

def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s

def _extract_inner_from_page_locator(s: str) -> str | None:
    m = re.search(r"(?:page\.)?locator\(\s*([\"'])(.+?)\1\s*\)", s)
    if m:
        return m.group(2)
    return None

def _looks_like_css(s: str) -> bool:
    s2 = s.strip()
    if not s2:
        return False
    if s2.startswith(CSS_START_TOKENS):
        return True
    for t in CSS_TAG_PREFIXES:
        if s2.startswith(t):
            return True
    if "[" in s2 and "]" in s2:
        return True
    return False



CSS_SIGNS = ('#','[','.','>',':','"',"'","\\","=",')','(')

def _normalize_selector(sel: str):
    s = (sel or "").strip()

    if ">>" in s or s.startswith("role="):
        return {"selector_engine": "locator", "selector": s, "arg": None}

    if s.startswith(("nav","div","span","button","input","a","ul","li","section","aside","main")):
        return {"selector_engine": "locator", "selector": s, "arg": None}
    if any(ch in s for ch in CSS_SIGNS):
        return {"selector_engine": "locator", "selector": s, "arg": None}

    if s.lower().startswith("text="):
        return {"selector_engine": "locator", "selector": s, "arg": None}

    return {"selector_engine": "get_by_text", "selector": s, "arg": s}





def get_next_action(user_task, visible_text_or_html, previous_action_result, latest_screenshot_path: str | None):
    """
    Decide the next action. Returns a validated dict:
      {
        "action": "click|fill|press|navigate|done",
        "selector": "<selector string for locator OR get_by_* expression>",
        "value": "<text or url or key>",
        "take_screenshot": true|false,
        "screenshot_description": "<short description>"
      }
    """
    system_prompt = """
    You are a web automation planner controlling a Playwright browser.
    When an image is provided, treat it as the current UI state and use it to choose precise selectors (chips, popovers, current values). Still return a SINGLE JSON object.
    Before clicking a chip like Status/Priority, visually check in the image whether it already shows the requested value (e.g., the chip text reads “In Progress” or “High”). If it already matches, do NOT click it again; move to the next property.

    Return a SINGLE JSON object ONLY (no code fences, no commentary) with this exact schema:
    {
    "action": "click" | "fill" | "press" | "navigate" | "request_input" | "done",
    "selector": "Selector string for Playwright page.locator(), e.g. 'text=Sign in', '#submit', 'input[type=\"email\"]'. DO NOT return Playwright code like page.get_by_text(...). If you intend get_by_text, return just the inner text and the executor will normalize.",
    "value": "string (for 'fill' -> typed text, 'press' -> key like 'Enter', 'navigate' -> URL; leave empty for 'click'/'done'/'request_input')",
    "take_screenshot": true | false,
    "screenshot_description": "short sentence describing this step",

    // Only required when action == "request_input":
    "field": "email | password | otp | code | phone | username | custom",
    "prompt": "short instruction to display in terminal (e.g., 'Enter your Linear login code')",
    "mask": true | false,
    "persist_key": "string key to cache/reuse the value in-session (e.g., 'linear.email')"
    }

    Rules:
    - NEVER return Playwright call expressions (no page.get_by_*; no code). Only return selectors or plain inner text.
    - Prefer robust selectors:
      - Text for visible buttons/links: e.g., 'text=Continue with email', 'text=Create project'
      - CSS for fields: '#email', '.email', '[data-testid=login]', 'input[type=\"email\"]', 'input[name*=\"email\"]',
        'input[autocomplete=\"one-time-code\"]', 'input[placeholder*=\"Email\" i]'
      - When strict-mode finds multiple matches, refine the selector (e.g., add container id, :has-text(), nth-of-type).
      - Never use aside:has-text(...) chains for navigation. Prefer role links, e.g. role=link[name=/Projects/i].
      - For Status/Priority/Labels/Start/Target, open the control by its property label (e.g., role=button[name=/Status/i]), not by the current value text (‘Backlog’, ‘No priority’).
      - (1) Open role=button|combobox[name=/Status|Priority/i] (scoped to the dialog). (2) Select with role=menuitem|option[name=/DesiredValue/i]. Don’t click the calendar or other chips until the target menu is open.

    Form-field selectors (very important):
    - For rich text / contenteditable description fields, prefer one of:
      - role+name: a role=textbox with accessible name including 'description' or 'summary'
        (we will normalize 'textbox name=/description|summary/i' to a selector)
      - aria-label: '[aria-label=\"Project description\"]'
      - contenteditable+aria: 'div[contenteditable=\"true\"][aria-label*=\"description\" i]'
    - Do NOT reuse the Name/Title selector when filling Description. Use a description/summary-targeted selector.
    - If the text to fill is long (paragraph-like), assume it belongs in the description/summary field and choose a description-specific selector.
    - Modal/dialog scoping (very important): When a dialog/modal is shown (e.g., 'Create project'), scope selectors to the dialog.
      Prefer role+name patterns like: dialog name=/create project/i + textbox name=/project name/i
      Or use a scoped string selector such as: \"role=dialog[name=/create project/i] >> [aria-label='Project name']\"
    - Prefer ARIA over placeholder in Linear project modals:
      - Name: '[aria-label=\"Project name\"]' or textbox name=/project name/i
      - Summary/Description: '[aria-label=\"Project description\"]', '[aria-label=\"Project summary\"]', or textbox name=/description|summary/i
    - After clicking 'Add project' (or similar), do not click it again; wait for the dialog and target fields inside it.
    - Avoid placeholder selectors on Linear project modals; use ARIA label or role+name.
    - If a field already displays the intended value, do NOT return another 'fill' for it; proceed to the next field.
🔧  - Also, if previous_action_result shows you just filled the same selector with the same value, do NOT issue another 'fill' for it.

    Error-recovery:
    - If previous_action_result indicates timeout/not found/strict-mode conflict, propose an ALTERNATIVE, more specific selector
      (switch text→CSS, add aria-label/name/role, or narrow with :has-text / nth).
    - Avoid bouncing between the same two fields. If a fill failed, return a refined selector; do not repeat the exact same one.

    Credentials / codes:
    - Use 'request_input' whenever the UI requires user credentials or codes (email/password/OTP). Include selector + field + prompt + mask + persist_key.
    - After a 'request_input':
      (a) you may return a 'fill' with the same selector (the executor will have the value), OR
      (b) return only 'request_input' and the executor will perform the fill immediately.

    Done Criteria (strict):
    - Only return "done" if the visible page clearly shows the user's goal is achieved (e.g., a project detail page, dashboard with the new item, an explicit success message).
    - Never return "done" on authentication/verification/code-entry screens. If uncertain, do NOT return "done".

    Property-setting planner (generalizable across apps):
    - After primary fields (e.g., Name/Title and Description) are set, satisfy any additional properties requested by the user task.
    - Treat the user task as possibly semi-structured; if it includes key:value pairs (e.g., Status: Active, Priority: High,
      Start date: 2025-11-03, Target date: 2025-11-17, Labels: [AgentDemo, Automation]), set each property exactly once.
    - Idempotency: if the UI already shows the requested value, do NOT set it again; move to the next property.

    How to set common property types (use ARIA roles/names; scope inside a visible dialog if one is open):
      • Enum / dropdown (e.g., Status, Priority):
        - Open the control by a button/combobox whose accessible name contains the property (e.g., 'Status', 'Priority').
          Examples: role=dialog[...] >> role=button[name=/status/i]  OR  role=combobox[name=/priority/i]
🔧       - Then select the desired option by visible text via role=menuitem **or role=option** (listbox), or fall back to dialog-scoped text=VALUE if no role is exposed.
        - Confirm the chip/control now displays the desired value; if so, do not repeat.

      • Date / datetime (e.g., Start/Begin, End/Target/Due):
        - Open a date control by a button or textbox whose name contains ('start'|'begin'|'from') or ('end'|'target'|'due'|'to').
🔧       - Prefer selecting from the calendar grid; if a textbox is present, you may type ISO YYYY-MM-DD **and press Enter**.
        - Verify the control reflects the selected date; skip if already correct.

      • Labels / tags / categories:
        - Open a control named ('labels'|'tags'|'categories').
        - Select or create the requested labels by visible text; avoid duplicates (only add if not already present).
        - Close the picker once all requested labels are present.

      • Toggles / switches / checkboxes:
        - Use role=switch or role=checkbox with name matching the property. Set to the requested state only if different.

    Selector guidance (general):
      - Prefer role+name (accessible name) or aria-label over placeholder for reliability.
      - If multiple matches exist for a generic selector (e.g., '[contenteditable="true"]', 'role=textbox'), refine using accessible name,
        container scoping (e.g., a dialog), or data-testid. Do NOT pick an arbitrary first match; return a refined, unambiguous selector instead.
      - If a field already displays the intended value, do NOT return another 'fill' for it; proceed to the next field.

    - For chip-like fields (Status, Priority, Start, Target, Labels), ALWAYS do two steps:
    1) Click the chip/button inside the dialog, e.g. 'role=dialog[name=/create|new project/i] >> role=button[name=/Status/i]' or the specific current value button (e.g., 'role=button[name=/Backlog/i]').
🔧  2) Then select from the opened menu using 'role=menuitem[name=/Active|High|On track/i]' or 'role=option[name=/.../i]'; if neither role is present, use dialog-scoped 'text=VALUE'. Do not return 'done' after step 1.

    - When submitting the form, prefer a dialog-scoped button:
    'role=dialog[name=/create|new project/i] >> role=button[name=/Create project|Create/i]'

    - For the Status/Health/Priority/Labels row in the project dialog, if a role=button selector fails, target the chip by visible text as a button inside the dialog, e.g. role=dialog[name=/create|new project/i] >> button:has-text("Backlog") (not bare text=Backlog).

    Completion:
      - Only return "done" after all requested properties are satisfied and the UI clearly reflects the completed state
        (e.g., a detail page, confirmation, or the dialog shows all requested values).
    """




    user_prompt = f"""
    User task: {user_task}

    Visible page (truncated to 6000 chars):
    {visible_text_or_html[:6000]}

    Previous action result: {previous_action_result or 'None'}

    Return ONLY the JSON object described above.
    """

    user_blocks = [
        {"type": "input_text", "text": f"Task:\n{user_task}"},
        {"type": "input_text", "text": f"Visible text:\n{visible_text_or_html[:8000]}"},
        {"type": "input_text", "text": f"Previous action result:\n{previous_action_result or 'None'}"},
    ]

    if latest_screenshot_path:
        data_url = image_to_data_url(latest_screenshot_path)
        user_blocks.append({"type": "input_image", "image_url": data_url})

      
    resp = client.responses.create(
        model="gpt-5",  
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": user_blocks},
        ],
        reasoning={"effort": "low"},
        text={"verbosity": "low"},
    )

    raw = (resp.output_text or "").strip()

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        raw = m.group(0)

    try:
        action = json.loads(raw)
    except Exception:
        return {"action": "done", "selector": "", "value": "", "take_screenshot": False, "screenshot_description": ""}

    action.setdefault("action", "done")
    action.setdefault("selector", "")
    action.setdefault("value", "")
    action.setdefault("take_screenshot", False)
    action.setdefault("screenshot_description", "")

    if action["action"] not in ALLOWED_ACTIONS:
        action["action"] = "done"

    norm = _normalize_selector(action.get("selector", ""))
    action["_selector_engine"] = norm["selector_engine"]
    action["_normalized_selector"] = norm["selector"]
    action["_get_by_arg"] = norm["arg"]

    return action
