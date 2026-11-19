// ============================================================================
// File:         TouchKeyboard_BV_CG.tsx
// Written by:   BV (Blas Vargas, the keyboard's design and behavior); CG
//               (Cristian Gonzalez, brought it back into src/)
// What it does: The on-screen keyboard. The kiosk has no physical keyboard, so
//               tapping any text field opens this one at the bottom of the
//               screen. A number pad opens for phone, ZIP, year and date
//               fields.
// Team-written: Logic written by BV; it was in the compiled form the Pi ran
//               until Nov 17 but not in src/, so the Nov 17 rebuild from src/
//               dropped it. CG rewrote it from that compiled form as readable
//               source, same keys and behavior, plus the number-pad marker
//               (data-keypad) tagged [CG].
// Third-party:  React.
// ============================================================================
import { useEffect, useRef, useState } from "react";

const TYPED = new Set(["text", "email", "tel", "password", "search", "url"]);

function typeable(target: EventTarget | null): target is HTMLInputElement | HTMLTextAreaElement {
  return (target instanceof HTMLTextAreaElement || (target instanceof HTMLInputElement && TYPED.has(target.type)))
    && !target.disabled && !target.readOnly;
}

// types one key into the field the way a real keyboard would, so React's onChange (and its formatting) still runs
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

// [CG] a field asks for the number pad with data-keypad="numeric" (InputEl sets it); the old name/placeholder guess stays as a fallback
function wantsNumbers(field: HTMLInputElement | HTMLTextAreaElement): boolean {
  return field.dataset.keypad === "numeric" || field.type === "tel" || /zip|postal|year/i.test(field.name + field.id + field.placeholder);
}

export function TouchKeyboard() {
  const [field, setField] = useState<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const [shift, setShift] = useState(false);
  const [symbols, setSymbols] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // stop the system's own keyboard from opening too
    const quiet = () => document.querySelectorAll("input, textarea").forEach(element => {
      if (typeable(element)) (element as HTMLInputElement).inputMode = "none";
    });
    quiet();
    const watcher = new MutationObserver(() => { quiet(); setField(current => current?.isConnected ? current : null); });
    watcher.observe(document.getElementById("root")!, { childList: true, subtree: true });
    const focusIn = (event: FocusEvent) => {
      if (typeable(event.target)) { setField(event.target); setSymbols(false); setShift(false); }
      // [CG] a button taking focus doesn't close the keyboard here (Chromium focuses it on touch-down); the click handler does
    };
    const pointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && panel.current?.contains(event.target)) return;
      if (typeable(event.target)) setField(event.target);
    };
    // [CG] tapping anything else closes the keyboard, but only once the tap is finished. Closing it on touch-down (as the
    // earlier build did) shrank the page under the finger, so a tap on Continue while typing could miss the button
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

  // keeps the field being typed in visible above the keyboard
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
