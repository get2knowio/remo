import { describe, expect, it } from "vitest";
import { inputForKeyEvent, isCopyChord } from "./keymap";

function key(init: KeyboardEventInit & { type?: string }): KeyboardEvent {
  const { type = "keydown", ...rest } = init;
  return new KeyboardEvent(type, rest);
}

describe("inputForKeyEvent", () => {
  it("maps Shift+Enter keydown to ESC+CR (newline, not submit)", () => {
    expect(inputForKeyEvent(key({ key: "Enter", shiftKey: true }))).toBe("\x1b\r");
  });

  it("leaves a plain Enter to the engine (submit)", () => {
    expect(inputForKeyEvent(key({ key: "Enter" }))).toBeNull();
  });

  it("only fires on keydown, not keypress/keyup", () => {
    expect(inputForKeyEvent(key({ key: "Enter", shiftKey: true, type: "keyup" }))).toBeNull();
    expect(inputForKeyEvent(key({ key: "Enter", shiftKey: true, type: "keypress" }))).toBeNull();
  });

  it("ignores Shift+Enter combined with another modifier", () => {
    expect(inputForKeyEvent(key({ key: "Enter", shiftKey: true, ctrlKey: true }))).toBeNull();
    expect(inputForKeyEvent(key({ key: "Enter", shiftKey: true, altKey: true }))).toBeNull();
    expect(inputForKeyEvent(key({ key: "Enter", shiftKey: true, metaKey: true }))).toBeNull();
  });

  it("ignores ordinary keys", () => {
    expect(inputForKeyEvent(key({ key: "a", shiftKey: true }))).toBeNull();
  });
});

describe("isCopyChord", () => {
  it("recognizes ⌘C and Ctrl+Shift+C", () => {
    expect(isCopyChord(key({ key: "c", metaKey: true }))).toBe(true);
    expect(isCopyChord(key({ key: "C", ctrlKey: true, shiftKey: true }))).toBe(true);
  });

  it("leaves bare Ctrl+C alone (stays SIGINT)", () => {
    expect(isCopyChord(key({ key: "c", ctrlKey: true }))).toBe(false);
  });

  it("ignores other keys, plain c, and non-keydown events", () => {
    expect(isCopyChord(key({ key: "v", metaKey: true }))).toBe(false);
    expect(isCopyChord(key({ key: "c" }))).toBe(false);
    expect(isCopyChord(key({ key: "c", metaKey: true, type: "keyup" }))).toBe(false);
  });
});
