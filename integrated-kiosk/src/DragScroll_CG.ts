// ============================================================================
// File:         DragScroll_CG.ts
// Written by:   CG (Cristian Gonzalez)
// What it does: Lets a finger drag anywhere on the form scroll it, like a
//               phone. On the kiosk Pi, Chromium can treat the touchscreen as
//               a mouse, and then only the thin scrollbar on the side scrolls.
//               Real touch input is left alone (the browser scrolls it
//               itself), so this only acts on mouse-style input.
// Team-written: All logic written by CG (Cristian Gonzalez).
// Third-party:  None (browser pointer events).
// ============================================================================

const START_AFTER_PX = 10;  // a tap that wobbles less than this is still a tap

// the nearest box that can scroll up/down under the finger (the camera pop-up scrolls on its own), else the page
function scroller(target: Element | null): Element {
  for (let node = target; node && node !== document.body; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight) return node;
  }
  return document.scrollingElement || document.documentElement;
}

// fields, lists and the touch keyboard keep their normal mouse behavior (placing the cursor, picking an option)
function leaveAlone(target: Element | null): boolean {
  return Boolean(target?.closest("input, textarea, select, option, [contenteditable=true], .touch-keyboard"));
}

export function enableDragScroll(root: Document = document): () => void {
  let drag: { id: number; startY: number; lastY: number; box: Element; moved: boolean } | null = null;
  let swallowClick = false;

  const down = (event: PointerEvent) => {
    if (event.pointerType !== "mouse" || event.button !== 0) return;  // real touch: the browser already scrolls
    const target = event.target as Element | null;
    if (leaveAlone(target)) return;
    drag = { id: event.pointerId, startY: event.clientY, lastY: event.clientY, box: scroller(target), moved: false };
    swallowClick = false;
  };
  const move = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.id) return;
    if (!drag.moved && Math.abs(event.clientY - drag.startY) < START_AFTER_PX) return;
    drag.moved = true;
    drag.box.scrollTop -= event.clientY - drag.lastY;  // finger moves up, page moves up: same as a phone
    drag.lastY = event.clientY;
    event.preventDefault();
    root.getSelection?.()?.removeAllRanges();  // a drag shouldn't leave text highlighted
  };
  const up = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.id) return;
    swallowClick = drag.moved;  // a drag that started on a button must not press it when the finger lifts
    drag = null;
  };
  const click = (event: MouseEvent) => {
    if (!swallowClick) return;
    swallowClick = false;
    event.preventDefault();
    event.stopPropagation();
  };

  root.addEventListener("pointerdown", down, true);
  root.addEventListener("pointermove", move, { capture: true, passive: false });
  root.addEventListener("pointerup", up, true);
  root.addEventListener("pointercancel", up, true);
  root.addEventListener("click", click, true);
  return () => {
    root.removeEventListener("pointerdown", down, true);
    root.removeEventListener("pointermove", move, true);
    root.removeEventListener("pointerup", up, true);
    root.removeEventListener("pointercancel", up, true);
    root.removeEventListener("click", click, true);
  };
}
