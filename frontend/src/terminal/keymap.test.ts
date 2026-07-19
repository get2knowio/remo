import { describe, expect, it } from "vitest";
import { inputForKeyEvent } from "./keymap";

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
