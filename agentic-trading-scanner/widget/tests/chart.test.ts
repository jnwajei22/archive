import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarketChartWidget, toUtcTimestamp, type ChartPayload } from "../src/chart";

const payload = (overrides: Partial<ChartPayload> = {}): ChartPayload => ({
  symbol: "EURUSD", timeframe: "1H", source: "tradelocker", actual_start: "2026-07-10T00:00:00Z", actual_end: "2026-07-10T01:00:00Z", complete: true,
  chart_type: "candlestick", show_volume: true,
  candles: [{ timestamp: "2026-07-10T00:00:00Z", open: 1.1, high: 1.2, low: 1, close: 1.15, volume: 12 }],
  horizontal_overlays: [], line_overlays: [], markers: [], ...overrides,
});

function harness() {
  const series = { setData: vi.fn(), createPriceLine: vi.fn(), priceScale: () => ({applyOptions: vi.fn()}) };
  const chart = { addSeries: vi.fn(() => series), timeScale: () => ({fitContent: vi.fn()}), subscribeCrosshairMove: vi.fn(), resize: vi.fn(), remove: vi.fn() };
  const root = document.createElement("div");
  const widget = new MarketChartWidget(root, vi.fn(() => chart) as never, vi.fn() as never);
  return { widget, root, chart, series };
}

describe("market chart widget", () => {
  beforeEach(() => { document.body.dataset.theme = "light"; });
  it("converts ISO UTC candles to seconds", () => expect(toUtcTimestamp("2026-07-10T00:00:00Z")).toBe(1783641600));
  it("receives tool-result metadata and creates a chart", () => { const h=harness(); h.widget.renderResult({_meta:{chart:payload()}}); expect(h.root.querySelector(".chart")).not.toBeNull(); expect(h.chart.addSeries).toHaveBeenCalled(); expect(h.series.setData).toHaveBeenCalled(); });
  it("shows an error for empty candles", () => { const h=harness(); h.widget.render(payload({candles:[]})); expect(h.root.textContent).toContain("No valid candle"); });
  it("shows an incomplete warning", () => { const h=harness(); h.widget.render(payload({complete:false, warning:"Partial history"})); expect(h.root.textContent).toContain("Partial history"); });
  it("accepts horizontal, line, and marker annotations", () => { const h=harness(); h.widget.render(payload({horizontal_overlays:[{label:"Fib",price:1.1,line_style:"dashed",line_width:1}],line_overlays:[{label:"EMA",line_style:"solid",line_width:2,points:[{timestamp:"2026-07-10T00:00:00Z",value:1.1}]}],markers:[{timestamp:"2026-07-10T00:00:00Z",position:"above",shape:"circle",label:"High"}]})); expect(h.series.createPriceLine).toHaveBeenCalled(); expect(h.chart.addSeries).toHaveBeenCalledTimes(3); });
  it("skips malformed data without crashing", () => { const h=harness(); expect(() => h.widget.render(payload({candles:[...payload().candles,{timestamp:"bad",open:NaN,high:2,low:1,close:1.2}]}))).not.toThrow(); });
  it("cleans up observer and chart", () => {
    const disconnect = vi.fn();
    vi.stubGlobal("ResizeObserver", class { observe = vi.fn(); unobserve = vi.fn(); disconnect = disconnect; });
    const h=harness(); h.widget.render(payload()); h.widget.destroy();
    expect(disconnect).toHaveBeenCalled(); expect(h.chart.remove).toHaveBeenCalled();
  });
});
