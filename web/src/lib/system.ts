// The words that depend on the system Dowse runs on: Windows, macOS or Linux (from the app's state).
import { dowse } from "./app.svelte";

export type System = "windows" | "mac" | "linux";

export function system(): System {
  return dowse.state?.system ?? "windows";
}

/** "Windows", "macOS", or the Linux desktop's (its own theme setting). */
export function systemName(): string {
  return { windows: "Windows", mac: "macOS", linux: "your desktop" }[system()];
}

/** Where Dowse waits when no window is open: the tray, or a Mac's menu bar. */
export function trayName(): string {
  return system() === "mac" ? "the menu bar" : "the tray";
}

/** The switch that starts Dowse when the user signs in. */
export function startupName(): string {
  return system() === "windows" ? "Start with Windows" : "Open at login";
}

/** Explorer, Finder, or the Linux desktop's file manager. */
export function fileManager(): string {
  return { windows: "Explorer", mac: "Finder", linux: "your file manager" }[system()];
}

/** Whether files and folders can be added from the file manager's right-click menu (Explorer's, for now). */
export function hasFileMenu(): boolean {
  return system() === "windows";
}
