import { vi } from "vitest";

class TestResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  constructor(public callback: ResizeObserverCallback) {}
}
vi.stubGlobal("ResizeObserver", TestResizeObserver);
