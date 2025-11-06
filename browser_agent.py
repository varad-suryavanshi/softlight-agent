# browser_agent.py
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
import re
import sys
import time
from collections import deque


def _dialog_is_open(page) -> bool:
    return bool(_visible_dialog(page))




def _ensure_no_popover(page):
    if _popup_is_open(page):
        try:
            page.keyboard.press("Escape")
            time.sleep(0.05)
        except Exception:
            pass


def _open_property_chip(page, label_regex: str) -> bool:
    """
    Open a property picker (e.g., Status, Priority) inside the *currently visible* dialog.

    Strategy:
    1) Ensure no other popover is open (date picker, another menu).
    2) Find the row group labelled by the property, then click its button/combobox.
    3) Fallbacks: role=button/combobox by accessible name; then row by text + button.
    4) Last resort: if only the *current value* is visible (e.g., Backlog/No priority),
       click that chip but still treat it as the property opener.
    """
    dlg = _visible_dialog(page)
    if not dlg:
        return False

    _ensure_no_popover(page)

    pat = re.compile(label_regex, re.I)

    def _click_and_wait(opener) -> bool:
        try:
            opener.scroll_into_view_if_needed(timeout=500)
        except Exception:
            pass
        try:
            opener.click(timeout=1200)
        except Exception:
            try:
                opener.click(timeout=1200, force=True)
            except Exception:
                return False
        if _wait_any_popup(page, timeout=1500):
            return True
        try:
            page.keyboard.press("ArrowDown")
            if _wait_any_popup(page, timeout=800):
                return True
        except Exception:
            pass
        return False

    try:
        rows = dlg.get_by_role("group", name=pat)
        if not rows.count():
            rows = dlg.get_by_role("region", name=pat)
        if rows.count():
            row = rows.first
            for q in ("role=combobox", "role=button"):
                ctl = row.locator(q)
                if ctl.count():
                    if _click_and_wait(ctl.first):
                        return True
            btn = row.locator("button")
            if btn.count() and _click_and_wait(btn.first):
                return True
    except Exception:
        pass

    try:
        cmb = dlg.get_by_role("combobox", name=pat)
        if cmb.count() and _click_and_wait(cmb.first):
            return True
    except Exception:
        pass
    try:
        btn = dlg.get_by_role("button", name=pat)
        if btn.count() and _click_and_wait(btn.first):
            return True
    except Exception:
        pass

    try:
        container = dlg.locator(f'[role="group"]:has-text(/{label_regex}/i), [role="region"]:has-text(/{label_regex}/i)')
        if not container.count():
            container = dlg.locator(f':has-text(/{label_regex}/i)')
        if container.count():
            row = container.first
            for css in ("[role=combobox]", "button", "[role=button]"):
                ctl = row.locator(css)
                if ctl.count() and _click_and_wait(ctl.first):
                    return True
    except Exception:
        pass

    try:
        value_patterns = []
        if re.search(r"status", label_regex, re.I):
            value_patterns = [r"Backlog", r"Planned", r"In\s*Progress", r"Completed", r"Canceled|Cancelled"]
        elif re.search(r"priority", label_regex, re.I):
            value_patterns = [r"No\s*priority", r"Low", r"Medium", r"High", r"Urgent"]

        for vp in value_patterns:
            chip = dlg.get_by_role("button", name=re.compile(vp, re.I))
            if not chip.count():
                chip = dlg.locator(f'button:has-text(/{vp}/i)')
            if chip.count() and _click_and_wait(chip.first):
                return True
    except Exception:
        pass

    
    return False



CHIP_LABEL_HINTS = [
    r"status", r"priority", r"labels?", r"tags?",
    r"start|begin|from", r"target|due|end|to",
    r"owner|assignee|lead|members?", r"health"
]


def _open_chip_generic(page, sel: str) -> bool:
    name_pat, _ = _extract_role_name(sel)
    if name_pat and _open_property_chip(page, name_pat):
        return True
    for hint in CHIP_LABEL_HINTS:
        if re.search(hint, sel, re.I) and _open_property_chip(page, hint):
            return True
    m = re.search(r'>>\s*text\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s][^>]*))\s*$', sel, re.I)
    val = (m.group(1) or m.group(2) or m.group(3)).strip() if m else None
    if val:
        return _click_chip_in_dialog(page, val)
    return False





def _parse_value_from_selector(sel: str) -> str | None:
    """
    Extract a 'desired value' text from menuitem/option selectors:
      - role=menuitem[name=/In Progress/i]
      - role=option[name=/High/i]
      - >> text=In Progress
    Returns a plain string like "In Progress" or "High", or None.
    """
    m = re.search(r'name=/([^/]+)/i', sel, flags=re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'>>\s*text\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s].*))', sel, flags=re.I)
    if m:
        return (m.group(1) or m.group(2) or m.group(3)).strip().strip('"').strip("'")
    m = re.search(r'text\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s].*))', sel, flags=re.I)
    if m:
        return (m.group(1) or m.group(2) or m.group(3)).strip().strip('"').strip("'")
    return None

def _any_chip_has_value(page, desired_regex: str) -> bool:
    """
    Generic: scan all dialog chip/buttons and see if any already show desired_regex.
    Works even if we don't know which property (Status/Priority/etc.).
    """
    dlg = _visible_dialog(page)
    if not dlg:
        return False
    try:
        btns = dlg.get_by_role("button")
        n = btns.count()
        for i in range(min(n, 20)): 
            try:
                txt = _normalize_text(btns.nth(i).inner_text() or "")
                if txt and re.search(desired_regex, txt, re.I):
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False

def _popup_is_open(page) -> bool:
    try:
        if page.get_by_role("menu").count() > 0:
            return True
    except Exception:
        pass
    try:
        if page.get_by_role("listbox").count() > 0:
            return True
    except Exception:
        pass
    return False


def _extract_role_name(sel: str) -> tuple[str|None, bool]:
    m = re.search(r'name=/([^/]+)/i', sel, flags=re.I)
    if m:
        return m.group(1), True
    m = re.search(r'name="([^"]+)"', sel, flags=re.I)
    if m:
        return m.group(1), False
    return None, False




def _read_text_like_from_locator(loc) -> str:
    try:
        return loc.input_value()
    except Exception:
        try:
            return loc.inner_text()
        except Exception:
            return ""





LONG_TEXT_THRESHOLD = 40  

def _prefer_desc_textbox(page):
    try:
        loc = page.get_by_role("textbox", name=re.compile(r"(description|summary)", re.I))
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    try:
        loc = page.locator('[aria-label*="description" i], [aria-label*="summary" i]')
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    return None


def _is_dialog_scoped(sel: str) -> bool:
    s = (sel or "").lower()
    return ("role=dialog" in s) or (">>" in s)

def _visible_dialog(page):
    try:
        dlg = page.get_by_role("dialog")
        return dlg if dlg.count() > 0 else None
    except Exception:
        return None

def _normalize_text(s: str) -> str:
    return " ".join((s or "").split()).strip()

def _find_chip_in_dialog(page, prop_regex: str):
    """
    Return (chip_button_locator, inner_text) for a chip whose accessible name or visible text
    matches prop_regex (case-insensitive), or (None, "") if not found.
    """
    dlg = _visible_dialog(page)
    if not dlg:
        return None, ""
    try:
        btn = dlg.get_by_role("button", name=re.compile(prop_regex, re.I))
        if btn.count():
            return btn.first, (btn.first.inner_text() or "")
    except Exception:
        pass
    try:
        btn = dlg.locator(f'button:has-text(/\\b{prop_regex}\\b/i)')
        if btn.count():
            return btn.first, (btn.first.inner_text() or "")
    except Exception:
        pass
    try:
        el = dlg.get_by_text(re.compile(prop_regex, re.I))
        if el.count():
            return el.first, (el.first.inner_text() or "")
    except Exception:
        pass
    return None, ""

def _chip_text_in_dialog(page, prop_regex: str) -> str:
    btn, txt = _find_chip_in_dialog(page, prop_regex)
    if not btn:
        return ""
    try:
        txt = btn.inner_text() or txt
    except Exception:
        pass
    return _normalize_text(txt)

def _chip_has_value(page, prop_regex: str, desired_value_regex: str) -> bool:
    txt = _chip_text_in_dialog(page, prop_regex)
    return bool(txt and re.search(desired_value_regex, txt, re.I))


def _click_chip_in_dialog(page, text_val: str) -> bool:
    dlg = _visible_dialog(page)
    if not dlg: return False
    try:
        btn = dlg.get_by_role("button", name=re.compile(re.escape(text_val), re.I))
        if btn.count():
            btn.first.click()
            _wait_any_popup(page, timeout=1500)
            return True
    except Exception:
        pass
    try:
        btn = dlg.locator(f'button:has-text("{text_val}")')
        if btn.count():
            btn.first.click()
            _wait_any_popup(page, timeout=1500)
            return True
    except Exception:
        pass
    try:
        el = dlg.get_by_text(text_val, exact=False)
        if el.count():
            el.first.click()
            _wait_any_popup(page, timeout=1500)
            return True
    except Exception:
        pass
    return False


def _wait_any_popup(page, timeout=4000) -> bool:
    """
    Wait briefly for dropdown popover (menu/listbox). Returns True if visible.
    """
    try:
        m = page.get_by_role("menu")
        if m.count():
            m.first.wait_for(state="visible", timeout=timeout)
            return True
    except Exception:
        pass
    try:
        lb = page.get_by_role("listbox")
        if lb.count():
            lb.first.wait_for(state="visible", timeout=timeout)
            return True
    except Exception:
        pass
    return False






def _select_from_popup(page, value: str) -> bool:
    """
    Selects an item from the currently-open popup (menu/listbox) regardless of
    role differences (menuitemradio/menuitem/option), virtualization, or filters.
    """
    pat = re.compile(re.escape(value), re.I)

    def _try_click(loc) -> bool:
        try:
            if loc.count():
                el = loc.first
                try:
                    el.scroll_into_view_if_needed(timeout=500)
                except Exception:
                    pass
                try:
                    el.click(timeout=1000)
                except Exception:
                    el.click(timeout=1000, force=True)
                return True
        except Exception:
            pass
        return False

    try:
        page.wait_for_selector('[role="menu"], [role="listbox"], [data-animated-popover-content]', timeout=1500)
    except Exception:
        return False

    for role in ("menu", "listbox"):
        try:
            cnt = page.get_by_role(role)
            if not cnt.count():
                continue
            for item_role in ("menuitemradio", "menuitem", "option"):
                loc = cnt.get_by_role(item_role, name=pat)
                if _try_click(loc):
                    return True
            loc = cnt.get_by_text(value, exact=False)
            if _try_click(loc):
                return True
        except Exception:
            pass

    try:
        pop = page.locator('[data-animated-popover-content]')
        if pop.count():
            for item_role in ("menuitemradio", "menuitem", "option"):
                loc = pop.get_by_role(item_role, name=pat)
                if _try_click(loc):
                    return True
            loc = pop.get_by_text(value, exact=False)
            if _try_click(loc):
                return True
    except Exception:
        pass

    try:
        filt = page.locator('[role="menu"] input,[role="listbox"] input,[data-animated-popover-content] input').first
        if filt.count():
            try:
                filt.fill(value)
            except Exception:
                filt.type(value)
            try:
                page.keyboard.press("Enter")
                return True
            except Exception:
                pass
    except Exception:
        pass

    for item_role in ("menuitemradio", "menuitem", "option"):
        try:
            loc = page.get_by_role(item_role, name=pat)
            if _try_click(loc):
                return True
        except Exception:
            pass

    try:
        loc = page.get_by_text(value, exact=False)
        if _try_click(loc):
            return True
    except Exception:
        pass

    try:
        for role_parent, role_item in (("menu","menuitem"),
                                       ("menu","menuitemradio"),
                                       ("listbox","option")):
            parent = page.get_by_role(role_parent)
            if parent.count():
                item = parent.get_by_role(role_item, name=re.compile(re.escape(value), re.I))
                if item.count():
                    item.first.click()
                    return True
        dlg = _visible_dialog(page)
        if dlg:
            el = dlg.get_by_text(re.compile(re.escape(value), re.I))
            if el.count():
                el.first.click()
                return True
    except Exception:
        pass
    return False




    

def _is_generic_selector(sel: str) -> bool:
    s = (sel or "").strip().lower()
    generics = (
        'div[contenteditable="true"]',
        '[contenteditable="true"]',
        'textarea',
        'input',
        'role=textbox',
        '[role="textbox"]',
    )
    return any(g in s for g in generics)



def _top_dialog_name(page) -> str:
            try:
                d = page.get_by_role("dialog")
                if d.count():
                    return _normalize_text(d.first.inner_text() or "")
            except Exception:
                pass
            return ""



_TEXT_PAT = r'(?:"([^"]+)"|\'([^\']+)\'|([^\s][^>]*))'

def _normalize_nav_selector(sel: str) -> str:
    s = sel or ""
    m = re.search(
        rf'aside:has-text\(\s*"(?:your teams|workspace)"\s*\)\s*>>\s*text\s*=\s*{_TEXT_PAT}',
        s, re.I
    )
    if m:
        label = next((g for g in m.groups() if g), "").strip()
        if label:
            return f'role=link[name=/^{re.escape(label)}$/i]'

    m = re.search(rf'nav\s*>>\s*text\s*=\s*{_TEXT_PAT}', s, re.I)
    if m:
        label = next((g for g in m.groups() if g), "").strip()
        if label:
            return f'role=link[name=/^{re.escape(label)}$/i]'

  
    if re.search(r'\btext\s*=\s*', s, re.I):
        m = re.search(rf'text\s*=\s*{_TEXT_PAT}', s, re.I)
        label = (next((g for g in m.groups() if g), "") if m else "").strip()
        if label and re.search(r'^(projects|issues|views|inbox|my issues)$', label, re.I):
            return f'role=link[name=/^{re.escape(label)}$/i]'

    m = re.search(rf'text\s*=\s*"Go to\s+({_TEXT_PAT})"', s, re.I)
    if m:
        raw = m.group(1) 
       
        if re.search(r'projects', s, re.I):
            return 'role=link[name=/^Projects$/i]'

    return sel



class BrowserAgent:
    def __init__(self, headless=False, default_timeout_ms=15000):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless)
        self.page = self.browser.new_page()
        self.page.set_default_timeout(default_timeout_ms)
        self.last_result = "Browser initialized."
        self._recent_clicks = deque(maxlen=100)  


    def click_menu_item(self, text_regex: str = ".*") -> bool:
        """Click the first menuitem whose accessible name matches text_regex (case-insensitive)."""
        try:
            menuitem = self.page.get_by_role("menuitem", name=re.compile(text_regex, re.I))
            if menuitem.count() > 0:
                menuitem.first.click()
                self.last_result = f"Selected menu item matching /{text_regex}/"
                return True
        except Exception:
            pass
        return False

    def _wait_visible(self, sel, timeout=15000):
        self.page.wait_for_selector(sel, state="visible", timeout=timeout)
    
    def navigate(self, url):
        self.page.goto(url)
        self.last_result = f"Navigated to {url}"

    def get_visible_text(self):
        try:
            return self.page.inner_text("body")
        except Exception:
            return ""

    def _should_debounce(self, sel: str, window_s: float = 2.0) -> bool:
        """
        Prevents tight loops clicking the exact same selector when state isn't changing.
        Returns True if we should skip this click due to recent identical attempts.
        """
        now = time.time()
        while self._recent_clicks and (now - self._recent_clicks[0][0]) > window_s:
            self._recent_clicks.popleft()
        recent_same = sum(1 for ts, s in self._recent_clicks if s == sel)
        if recent_same >= 2:  
            return True
        self._recent_clicks.append((now, sel))
        return False
    
    
    

    def execute_action(self, action):
        
        try:
            kind = action.get("action")
            engine = action.get("_selector_engine", "locator")
            sel = (action.get("_normalized_selector") or action.get("selector") or "").strip()
            arg = action.get("_get_by_arg")
            val = action.get("value", "")
            sel = _normalize_nav_selector(sel)
            lowered = (sel or "").lower()

            if kind == "click":

                lowered = (sel or "").lower()
                is_popup_item = ("role=menuitem" in lowered) or ("role=option" in lowered) or ("menuitemradio" in lowered)

                if is_popup_item:
                    desired = _parse_value_from_selector(sel)
                    if desired:
                        if _any_chip_has_value(self.page, re.escape(desired)):
                            self.last_result = f"Skipped selecting '{desired}': already set."
                            return True
                        if _select_from_popup(self.page, desired):
                            time.sleep(0.05)
                            for _ in range(5):
                                if not _popup_is_open(self.page):
                                    break
                                time.sleep(0.05)
                            if _any_chip_has_value(self.page, re.escape(desired)):
                                self.last_result = f"Selected '{desired}' from popup"
                            else:
                                self.last_result = f"Clicked '{desired}' from popup (unverified)"
                            return True

                dlg = _visible_dialog(self.page)
                sel_lower = (sel or "").lower()
                dialog_scoped = ("role=dialog" in sel_lower) or (">>" in sel)

                if dlg and not dialog_scoped:
                    try:
                        loc = dlg.locator(sel)
                        if loc.count():
                            loc.first.click()
                            self.last_result = f"Clicked (scoped to open dialog) {sel}"
                            return True
                    except Exception:
                        pass

                if dlg:
                    top = _top_dialog_name(self.page)
                    if re.search(r"(discard|delete|remove|unsaved|are you sure)", top, re.I):
                        if not dialog_scoped:
                            self.last_result = "Blocked click outside confirmation dialog while edit dialog is open."
                            return True
                        
                if dlg and not _popup_is_open(self.page) and _open_chip_generic(self.page, sel):
                    self.last_result = "Opened chip via dialog-scoped, label-first strategy"
                    return True


                if ("role=menuitem" in lowered) or ("role=option" in lowered) or ("menuitemradio" in lowered):
                    desired = _parse_value_from_selector(sel)
                    if desired:
                        if _any_chip_has_value(self.page, re.escape(desired)):
                            self.last_result = f"Skipped selecting '{desired}': already set."
                            return True
                        if _select_from_popup(self.page, desired):
                            time.sleep(0.05)
                            ok = True
                            for _ in range(5):
                                if not _popup_is_open(self.page):
                                    break
                                time.sleep(0.05)
                            if _any_chip_has_value(self.page, re.escape(desired)):
                                self.last_result = f"Selected '{desired}' from popup"
                            else:
                                self.last_result = f"Clicked '{desired}' from popup (unverified)"
                            return True

                try:
                    _submit_pat = r"(create|save|submit|confirm|finish|publish|done)"
                    is_dialog_scoped = ("role=dialog" in sel.lower()) or (">>" in sel)
                    looks_like_submit = (
                        is_dialog_scoped
                        and (
                            re.search(rf"(role=button|button:has-text)\(.*{_submit_pat}.*\)", sel, re.I)
                            or re.search(r'\b(text\s*=\s*)?"?(Create|Save|Submit|Confirm|Finish|Publish|Done)\b', sel, re.I)
                        )
                    )
                    name_pat, _ = _extract_role_name(sel)

                    if looks_like_submit and not _dialog_is_open(self.page):
                        self.last_result = "Skipped submit: dialog already closed (likely submitted)."
                        return True
                    
                    if self._should_debounce(sel):
                        self.last_result = f"Debounced repeat click on {sel}"
                        return True
                    

                    if engine == "get_by_text" and arg:
                        el = self.page.get_by_text(arg, exact=False)
                        (el.first if el.count() else el).wait_for(state="visible", timeout=15000)
                        (el.first if el.count() else el).click()
                        self.last_result = f"Clicked by text: {arg}"
                    else:
                        if ">> text=" in sel and "role=dialog" in sel:
                            try:
                                left, right = sel.split(">>", 1)
                                text_val = right.split("text=", 1)[1].strip().strip('"').strip("'")
                                dlg = _visible_dialog(self.page)
                                if dlg and text_val:
                                    btn = dlg.get_by_role("button", name=re.compile(re.escape(text_val), re.I))
                                    if btn.count() > 0:
                                        btn.first.click()
                                        self.last_result = f"Clicked dialog button matching '{text_val}'"
                                        return True
                                    btn = dlg.locator(f'button:has-text("{text_val}")')
                                    if btn.count() > 0:
                                        btn.first.click()
                                        self.last_result = f"Clicked dialog button:has-text('{text_val}')"
                                        return True
                                    el = dlg.get_by_text(text_val, exact=False)
                                    if el.count() > 0:
                                        (el.first if el.count() else el).click()
                                        self.last_result = f"Clicked dialog text '{text_val}' (generic)"
                                        return True
                            except Exception:
                                pass
                        try:
                            self._wait_visible(sel)
                            self.page.locator(sel).first.click()
                        except Exception:
                            m = re.search(r'>>\s*text\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s][^>]*))\s*$', sel, flags=re.I)
                            text_val = (m.group(1) or m.group(2) or m.group(3)).strip() if m else None
                            if text_val and _click_chip_in_dialog(self.page, text_val):
                                try:
                                    self.page.wait_for_selector('[role="menu"], [role="listbox"]', timeout=1500)
                                except Exception:
                                    pass
                                self.last_result = f"Clicked dialog chip/button '{text_val}'"
                                return True

                            

                            if not text_val and not name_pat:
                                try:
                                    self.page.wait_for_selector('[role="menu"], [role="listbox"]', timeout=1500)
                                except Exception:
                                    pass
                                for token in ("Backlog", "Status", "Health", "Priority", "Labels", "Start", "Target"):
                                    if token.lower() in sel.lower():
                                        if _click_chip_in_dialog(self.page, token):
                                            self.last_result = f"Clicked dialog chip/button '{token}'"
                                            return True
                                        break

                            if _is_dialog_scoped(sel):
                                if _popup_is_open(self.page):
                                    self.last_result = "Popup already open; skipping chip re-click."
                                    return True
                                self.page.locator(sel).first.click()
                                if not _wait_any_popup(self.page, timeout=800):  
                                    try:
                                        self.page.keyboard.press("Escape") 
                                    except Exception:
                                        pass
                                    if not _wait_any_popup(self.page, timeout=800):
                                        self.last_result = "Chip clicked but no popup appeared; treating as no-op."
                                        return True

                            else:
                                dlg = _visible_dialog(self.page)
                                if dlg:
                                    dlg.locator(sel).first.click()
                                else:
                                    self.page.locator(sel).first.click()

                        self.last_result = f"Clicked {sel}"
                except Exception as e:
                    self.last_result = f"Error executing action: {e}"
                    return True


            elif kind == "fill":
                filled = False

                if sel:
                    try:
                        loc = self.page.locator(sel)
                        count = 0
                        try:
                            count = loc.count()
                        except Exception:
                            count = 1

                        if count > 1 and _is_generic_selector(sel):
                            raise RuntimeError(
                                f"Ambiguous selector '{sel}'. Refine with aria-label or role+name "
                                f"(e.g., [aria-label='Project description'] or textbox name=/project name/i)."
                            )

                        if count == 0:
                            dlg = _visible_dialog(self.page)
                            if dlg:
                                loc = dlg.locator(sel)
                                try:
                                    count = loc.count()
                                except Exception:
                                    count = 0

                        if count > 1 and len(val) >= LONG_TEXT_THRESHOLD:
                            t = _prefer_desc_textbox(self.page)
                            if t:
                                t.fill(val)
                                self.last_result = f"Filled desc-like textbox with '{val[:30]}...'"
                                return True
                            loc.first.fill(val)
                            self.last_result = f"Filled first of multi-match {sel} with '{val[:30]}...'"
                            return True

                        if count >= 1:
                            el = loc.first
                            el.wait_for(state="visible", timeout=15000)

                            current = _normalize_text(_read_text_like_from_locator(el))
                            target  = _normalize_text(val)
                            if current and target and current == target:
                                self.last_result = f"Skipped fill for {sel}: already set."
                                return True

                            is_contenteditable = False
                            try:
                                ce = el.get_attribute("contenteditable")
                                is_contenteditable = ce in ("", "true")
                            except Exception:
                                pass

                            if is_contenteditable:
                                el.click()
                                import sys
                                mod = "Meta" if sys.platform == "darwin" else "Control"
                                self.page.keyboard.press(f"{mod}+A")
                                self.page.keyboard.press("Backspace")
                                el.type(val)  
                            else:
                                try:
                                    el.fill(val)    
                                except Exception:
                                    el.click()       
                                    el.type(val)

                            preview = (val[:30] + "...") if len(val) > 30 else val
                            self.last_result = f"Filled {sel} with '{preview}'"
                            filled = True
                    except Exception:
                        pass

                if not filled:
                    dlg = _visible_dialog(self.page)
                    root = dlg if dlg else self.page

                    if len(val) >= LONG_TEXT_THRESHOLD:
                        t = root.get_by_role("textbox", name=re.compile(r"(description|summary)", re.I))
                        if t.count() > 0:
                            t.first.fill(val)
                            self.last_result = f"Filled description textbox with '{val[:30]}...'"
                            filled = True
                        else:
                            t = root.locator('[aria-label="Project description"], [aria-label*="description" i], [aria-label*="summary" i]')
                            if t.count() > 0:
                                t.first.fill(val)
                                self.last_result = f"Filled aria description with '{val[:30]}...'"
                                filled = True
                    else:
                        t = root.get_by_role("textbox", name=re.compile(r"(project name|name)", re.I))
                        if t.count() > 0:
                            t.first.fill(val)
                            self.last_result = f"Filled name textbox with '{val[:30]}...'"
                            filled = True
                        else:
                            t = root.locator('[aria-label="Project name"], [aria-label*="name" i]')
                            if t.count() > 0:
                                t.first.fill(val)
                                self.last_result = f"Filled aria name with '{val[:30]}...'"
                                filled = True

                if not filled:
                    raise RuntimeError(f"Could not fill any field using selector='{sel}' arg='{arg}'")

            elif kind == "press":
                self.page.keyboard.press(val or "Enter")
                self.last_result = f"Pressed key {val or 'Enter'}"

            elif kind == "navigate":
                self.page.goto(val)
                self.last_result = f"Navigated to {val}"

            elif kind == "done":
                self.last_result = "Task completed."
                return False

            return True

        except PwTimeout as e:
            self.last_result = f"Timeout: {e}"
            return True
        except Exception as e:
            self.last_result = f"Error executing action: {e}"
            return True


    def screenshot(self, path):
        self.page.screenshot(path=path)

    def close(self):
        self.browser.close()
        self.playwright.stop()
