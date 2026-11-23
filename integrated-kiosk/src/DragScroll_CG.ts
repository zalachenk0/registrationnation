// ============================================================================
// File:         DragScroll_CG.ts
// Written by:   CG (Cristian Gonzalez)
// What it does: Drag a finger anywhere to scroll, like a phone. On the Pi,
//               Chromium can treat the touchscreen like a mouse, and then
//               only the scrollbar scrolls. Real touch input is left alone
//               since the browser already handles it.
// Team-written: All logic written by CG (Cristian Gonzalez).
// Third-party:  None (browser pointer events).
// ============================================================================

const START_AFTER_PX = 10;  // anything under 10px counts as a tap, not a drag

// nearest scrollable box under the finger (the camera pop-up scrolls on its own), otherwise the page
function scroller(target: Element | null): Element {
  for (let node = target; node && node !== document.body; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight) return node;
  }
  return document.scrollingElement || document.documentElement;
}

// fields, dropdowns and the touch keyboard act normal (cursor placement, picking options)
// [SL] the signature box too, or signing would scroll the page
function leaveAlone(target: Element | null): boolean {
  return Boolean(target?.closest("input, textarea, select, option, [contenteditable=true], .touch-keyboard, .signature-pad"));
}

export function enableDragScroll(root: Document = document): () => void {
  let drag: { id: number; startY: number; lastY: number; box: Element; moved: boolean } | null = null;
  let swallowClick = false;

  const down = (event: PointerEvent) => {
    if (event.pointerType !== "mouse" || event.button !== 0) return;  // real touch, browser handles it
    const target = event.target as Element | null;
    if (leaveAlone(target)) return;
    drag = { id: event.pointerId, startY: event.clientY, lastY: event.clientY, box: scroller(target), moved: false };
    swallowClick = false;
  };
  const move = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.id) return;
    if (!drag.moved && Math.abs(event.clientY - drag.startY) < START_AFTER_PX) return;
    drag.moved = true;
    drag.box.scrollTop -= event.clientY - drag.lastY;  // finger up, page up, same as a phone
    drag.lastY = event.clientY;
    event.preventDefault();
    root.getSelection?.()?.removeAllRanges();  // don't leave text highlighted after a drag
  };
  const up = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.id) return;
    swallowClick = drag.moved;  // a drag that started on a button shouldn't press it
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
