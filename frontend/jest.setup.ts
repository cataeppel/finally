import "@testing-library/jest-dom";

// jsdom implements neither of these; components guard on ResizeObserver but the
// charting library and scroll-to-bottom logic assume they exist.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {};
}
