import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// Recharts measures its container to lay charts out, and jsdom reports every
// element as 0x0 -- which makes ResponsiveContainer render nothing at all and
// every chart assertion vacuously fail. Give it a real box.
Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, value: 800 });
Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 400 });
Object.defineProperty(HTMLElement.prototype, "getBoundingClientRect", {
  configurable: true,
  value: () => ({ width: 800, height: 400, top: 0, left: 0, bottom: 400, right: 800, x: 0, y: 0 }),
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = globalThis.ResizeObserver ?? (ResizeObserverStub as never);
