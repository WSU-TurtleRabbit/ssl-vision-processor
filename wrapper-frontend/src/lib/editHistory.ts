// Shared helpers for the point-editing modes (field corners, lens
// correction): an undo/redo stack of immutable snapshots, keyboard-shortcut
// helpers and point maths.

export type Point = [number, number];

/** Undo/redo stack over immutable snapshots of an editor state. */
export class History<T> {
  private past: T[] = [];
  private future: T[] = [];

  constructor(private readonly limit = 200) {}

  /** Record `previous` as the state before a change. Clears redo. */
  push(previous: T): void {
    this.past.push(previous);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
  }

  /** Returns the state to restore, recording `current` for redo. */
  undo(current: T): T | null {
    const previous = this.past.pop();
    if (previous === undefined) return null;
    this.future.push(current);
    return previous;
  }

  redo(current: T): T | null {
    const next = this.future.pop();
    if (next === undefined) return null;
    this.past.push(current);
    return next;
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  clear(): void {
    this.past = [];
    this.future = [];
  }
}

/** True when the key event comes from a text field (shortcuts must not fire). */
export function isTypingTarget(event: KeyboardEvent): boolean {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (target as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "range"].includes(type);
  }
  return false;
}

/** Which editing shortcut a key event is, or null. */
export type Shortcut =
  | "undo"
  | "redo"
  | "enter"
  | "escape"
  | "removeLast"
  | "nudge";

export function shortcutOf(event: KeyboardEvent): Shortcut | null {
  const mod = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();
  if (mod && key === "z" && event.shiftKey) return "redo";
  if (mod && key === "z") return "undo";
  if (mod && key === "y") return "redo";
  if (mod) return null;
  if (event.key === "Enter") return "enter";
  if (event.key === "Escape") return "escape";
  if (event.key === "Backspace" || event.key === "Delete") return "removeLast";
  if (event.key.startsWith("Arrow")) return "nudge";
  return null;
}

/** Arrow-key delta in image px (1 px, Shift = 5 px). */
export function nudgeDelta(event: KeyboardEvent): Point {
  const step = event.shiftKey ? 5 : 1;
  switch (event.key) {
    case "ArrowLeft":
      return [-step, 0];
    case "ArrowRight":
      return [step, 0];
    case "ArrowUp":
      return [0, -step];
    case "ArrowDown":
      return [0, step];
    default:
      return [0, 0];
  }
}

/** Mouse event -> image pixels (0.1 px) inside an SVG that covers the image. */
export function toImagePoint(
  svg: SVGSVGElement | undefined,
  event: { clientX: number; clientY: number },
  width: number,
  height: number,
): Point | null {
  if (!svg) return null;
  const rect = svg.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  const x = ((event.clientX - rect.left) / rect.width) * width;
  const y = ((event.clientY - rect.top) / rect.height) * height;
  return [
    Math.round(Math.min(width, Math.max(0, x)) * 10) / 10,
    Math.round(Math.min(height, Math.max(0, y)) * 10) / 10,
  ];
}

export function movePoint(
  point: Point,
  delta: Point,
  width: number,
  height: number,
): Point {
  return [
    Math.round(Math.min(width, Math.max(0, point[0] + delta[0])) * 10) / 10,
    Math.round(Math.min(height, Math.max(0, point[1] + delta[1])) * 10) / 10,
  ];
}

/** Index of the point nearest to `target` within `maxDistance` px, or -1. */
export function nearestIndex(
  points: Point[],
  target: Point,
  maxDistance: number,
): number {
  let best = -1;
  let bestDistance = maxDistance;
  points.forEach((point, index) => {
    const distance = Math.hypot(point[0] - target[0], point[1] - target[1]);
    if (distance <= bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });
  return best;
}

/** The shortcut legend shown in the mode banners and in Help. */
export const SHORTCUTS: [string, string][] = [
  ["Click", "add a point"],
  ["Drag", "move a point"],
  ["Ctrl+click", "delete the nearest point"],
  ["Shift+click", "start a new line (lens mode)"],
  ["Arrows", "nudge the selected point 1 px (Shift: 5 px)"],
  ["Backspace / Delete", "remove the last point"],
  ["Ctrl+Z", "undo"],
  ["Ctrl+Y / Ctrl+Shift+Z", "redo"],
  ["Enter", "next line (lens) / save when complete"],
  ["Esc", "cancel"],
];
