// Shortcuts as each system has them: on a Mac, Command where Windows has Ctrl, and the keys' Mac names.

export const MAC = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform || navigator.userAgent);

/** Ctrl on Windows, Command on a Mac: the key held for a shortcut (Ctrl+O, Ctrl+click). */
export function primary(event: KeyboardEvent | MouseEvent): boolean {
  return MAC ? event.metaKey : event.ctrlKey;
}

/** How a shortcut is written: "Ctrl+O", "⌘O" on a Mac. */
export function shortcut(key: string): string {
  return MAC ? `⌘${key === "Enter" ? "↩" : key}` : `Ctrl+${key}`;
}

/** A key of the global shortcut (as settings store it: Alt, Win/Cmd…) by the name this system gives it. */
export function keyName(key: string): string {
  if (!MAC) return key;
  return ({ alt: "Option", win: "Command", cmd: "Command", ctrl: "Control" } as Record<string, string>)[key.toLowerCase()] ?? key;
}

/** The global shortcut's modifiers held in `event`, as settings store them. */
export function modifiers(event: KeyboardEvent): string[] {
  return (MAC
    ? [event.ctrlKey && "Ctrl", event.altKey && "Alt", event.shiftKey && "Shift", event.metaKey && "Cmd"]
    : [event.ctrlKey && "Ctrl", event.altKey && "Alt", event.shiftKey && "Shift", event.metaKey && "Win"]
  ).filter((m): m is string => Boolean(m));
}

/** The key pressed, by its place on the keyboard where it has one (Option+A types "å" on a Mac). */
export function keyOf(event: KeyboardEvent): string {
  if (event.code === "Space") return "Space";
  if (/^Key[A-Z]$/.test(event.code)) return event.code.slice(3);
  if (/^Digit\d$/.test(event.code)) return event.code.slice(5);
  return event.key.length === 1 ? event.key.toUpperCase() : event.key;
}
