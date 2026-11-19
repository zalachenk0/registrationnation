// ============================================================================
// File:         TouchKeyboard_BV_CG.tsx
// Written by:   BV (Blas Vargas, the keyboard itself); CG (Cristian Gonzalez,
//               put it back in src/ and fixed the missed-tap bug)
// What it does: On-screen keyboard. The kiosk has no real keyboard, so tapping
//               a text field pops this up at the bottom. Phone, ZIP, year and
//               date fields get a number pad.
// Team-written: Logic written by BV. It only existed in the old compiled build,
//               so the Nov 17 rebuild lost it; CG rewrote it from that build
//               with the same keys and behavior. CG's changes are tagged [CG].
// Third-party:  React.
// ============================================================================
import { useEffect, useRef, useState } from "react";

const TYPED = new Set(["text", "email", "tel", "password", "search", "url"]);

function typeable(target: EventTarget | null): target is HTMLInputElement | HTMLTextAreaElement {
  return (target instanceof HTMLTextAreaElement || (target instanceof HTMLInputElement && TYPED.has(target.type)))
    && !target.disabled && !target.readOnly;
}

// types a key like a real keyboard would, so React's onChange and formatting still fire
function press(field: HTMLInputElement | HTMLTextAreaElement, key: string) {
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  const from = key === "Backspace" && start === end ? Math.max(0, start - 1) : start;
  const text = key === "Backspace" ? "" : key;
  const next = field.value.slice(0, from) + text + field.value.slice(end);
  if (field.maxLength >= 0 && next.length > field.maxLength) return;
  const prototype = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(field, next);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  try { field.setSelectionRange(from + text.length, from + text.length); } catch { /* email fields have no cursor position */ }
}

// [CG] InputEl marks number fields with data-keypad="numeric". the old name/placeholder guess is the fallback
function wantsNumbers(field: HTMLInputElement | HTMLTextAreaElement): boolean {
  return field.dataset.keypad === "numeric" || field.type === "tel" || /zip|postal|year/i.test(field.name + field.id + field.placeholder);
}

export function TouchKeyboard() {
  const [field, setField] = useState<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const [shift, setShift] = useState(false);
  const [symbols, setSymbols] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // keeps the OS keyboard from popping up too
    const quiet = () => document.querySelectorAll("input, textarea").forEach(element => {
      if (typeable(element)) (element as HTMLInputElement).inputMode = "none";
    });
    quiet();
    const watcher = new MutationObserver(() => { quiet(); setField(current => current?.isConnected ? current : null); });
    watcher.observe(document.getElementById("root")!, { childList: true, subtree: true });
    const focusIn = (event: FocusEvent) => {
      if (typeable(event.target)) { setField(event.target); setSymbols(false); setShift(false); }
      // [CG] buttons get focus on touch-down in Chromium, so closing happens in the click handler instead
    };
    const pointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && panel.current?.contains(event.target)) return;
      if (typeable(event.target)) setField(event.target);
    };
    // [CG] close the keyboard after the tap finishes, not on touch-down. closing it early shifted the page under the
    // finger and taps on Continue would miss
    const tapped = (event: MouseEvent) => {
      if (event.target instanceof Node && panel.current?.contains(event.target)) return;
      if (!typeable(event.target)) setField(null);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setField(null); };
    document.addEventListener("focusin", focusIn);
    document.addEventListener("pointerdown", pointerDown);
    document.addEventListener("click", tapped);
    document.addEventListener("keydown", escape);
    return () => {
      watcher.disconnect();
      document.removeEventListener("focusin", focusIn);
      document.removeEventListener("pointerdown", pointerDown);
      document.removeEventListener("click", tapped);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  // keeps the field you're typing in visible above the keyboard
  useEffect(() => {
    if (!field || !panel.current) return;
    const resize = new ResizeObserver(() => {
      document.documentElement.style.setProperty("--touch-keyboard-height", `${panel.current!.offsetHeight + 16}px`);
      field.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    resize.observe(panel.current);
    return () => { resize.disconnect(); document.documentElement.style.removeProperty("--touch-keyboard-height"); };
  }, [field]);

  if (!field) return null;
  const numeric = wantsNumbers(field);
  const rows = numeric ? ["123", "456", "789", "+0-"]
    : symbols ? ["1234567890", "@#$%&*()-", "_+=/:;!?", ".,'\""]
    : ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"];
  const type = (key: string) => {
    if (!field.isConnected) { setField(null); return; }
    press(field, shift && !symbols && key !== "Backspace" ? key.toUpperCase() : key);
  };
  const close = () => { field.blur(); setField(null); };
  const key = (label: string, onPress: () => void, extra = "") =>
    <button key={label} type="button" className={`touch-key ${extra}`} onClick={onPress}>{label}</button>;

  return (
    <div ref={panel} className={`touch-keyboard ${numeric ? "touch-keyboard-numeric" : ""}`} role="region"
      aria-label="Touch keyboard" onPointerDown={event => event.preventDefault()}>
      <div className="touch-keyboard-bar">
        <span>Touch keyboard</span>
        {key("Hide keyboard", close)}
      </div>
      {rows.map((row, index) => (
        <div key={index} className="touch-keyboard-row touch-keyboard-characters">
          {Array.from(row).map(character => key(shift && !symbols ? character.toUpperCase() : character, () => type(character)))}
          {index === 0 && key("⌫", () => type("Backspace"), "touch-key-wide")}
        </div>
      ))}
      <div className="touch-keyboard-row">
        {!numeric && key(shift ? "SHIFT" : "Shift", () => setShift(!shift))}
        {!numeric && key(symbols ? "ABC" : "#+=", () => setSymbols(!symbols))}
        {!numeric && key("@", () => type("@"))}
        {!numeric && key("Space", () => type(" "), "touch-key-space")}
        {!numeric && key(".", () => type("."))}
        {key("Next", () => {
          const fields = [...document.querySelectorAll("input, textarea")].filter(
            (element): element is HTMLInputElement => typeable(element) && element.getClientRects().length > 0);
          const next = fields[fields.indexOf(field as HTMLInputElement) + 1];
          if (next) next.focus(); else close();
        })}
        {key("Done", close, "touch-key-done")}
      </div>
    </div>
  );
}
