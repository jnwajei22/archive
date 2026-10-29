import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers,
  HistogramSeries, LineSeries, LineStyle, type IChartApi, type ISeriesApi,
  type SeriesType, type UTCTimestamp,
} from "lightweight-charts";

export interface Candle { timestamp: string; open: number; high: number; low: number; close: number; volume?: number | null }
export interface ChartPayload {
  title?: string | null; symbol: string; timeframe: string; source: string;
  actual_start?: string | null; actual_end?: string | null; complete: boolean;
  warning?: string | null; chart_type: "candlestick" | "line"; show_volume: boolean;
  candles: Candle[]; horizontal_overlays: Array<{ label: string; price: number; line_style: string; line_width: 1|2|3 }>;
  line_overlays: Array<{ label: string; points: Array<{ timestamp: string; value: number }>; line_style: string; line_width: 1|2|3 }>;
  markers: Array<{ timestamp: string; position: "above"|"below"; shape: "circle"|"square"|"arrow_up"|"arrow_down"; label: string }>;
}

export interface ToolResult { isError?: boolean; _meta?: { chart?: ChartPayload } }
type ChartFactory = typeof createChart;
type MarkerFactory = typeof createSeriesMarkers;

export function toUtcTimestamp(value: unknown): UTCTimestamp | null {
  if (typeof value !== "string") return null;
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) return null;
  return Math.floor(milliseconds / 1000) as UTCTimestamp;
}

function uniqueByTime<T extends { time: UTCTimestamp }>(points: T[]): T[] {
  return [...new Map(points.map((point) => [point.time, point])).values()].sort((a, b) => a.time - b.time);
}

function finite(...values: unknown[]): values is number[] {
  return values.every((value) => typeof value === "number" && Number.isFinite(value));
}

function lineStyle(value: string): LineStyle {
  return value === "dashed" ? LineStyle.Dashed : value === "dotted" ? LineStyle.Dotted : LineStyle.Solid;
}

export class MarketChartWidget {
  private chart: IChartApi | null = null;
  private observer: ResizeObserver | null = null;
  private priceSeries: ISeriesApi<SeriesType> | null = null;

  constructor(
    private root: HTMLElement,
    private chartFactory: ChartFactory = createChart,
    private markerFactory: MarkerFactory = createSeriesMarkers,
  ) {}

  renderResult(result: ToolResult): void {
    if (result.isError) return this.showError("The chart tool returned an error.");
    const payload = result._meta?.chart;
    if (!payload) return this.showError("Chart data was unavailable.");
    this.render(payload);
  }

  render(payload: ChartPayload): void {
    this.destroyChart();
    const candles = payload.candles.flatMap((item) => {
      const time = toUtcTimestamp(item.timestamp);
      return time && finite(item.open, item.high, item.low, item.close) && item.high >= Math.max(item.open, item.close, item.low) && item.low <= Math.min(item.open, item.close, item.high)
        ? [{ time, open: item.open, high: item.high, low: item.low, close: item.close, volume: finite(item.volume) ? item.volume : undefined }]
        : [];
    });
    const uniqueCandles = uniqueByTime(candles);
    if (!uniqueCandles.length) return this.showError("No valid candle data was provided.");

    this.root.innerHTML = `<section class="card" aria-label="Interactive market chart"><header class="header"><h1 class="title"></h1><div class="meta"></div><div class="legend" aria-live="polite">Move the crosshair over a candle for OHLC details.</div></header><div class="warning" hidden></div><div class="chart" role="img" aria-label="${payload.symbol} ${payload.timeframe} interactive chart"></div></section>`;
    this.root.querySelector<HTMLElement>(".title")!.textContent = payload.title || `${payload.symbol} | ${payload.timeframe}`;
    this.root.querySelector<HTMLElement>(".meta")!.textContent = `${payload.source} | ${payload.actual_start ?? "?"} - ${payload.actual_end ?? "?"}`;
    const warning = this.root.querySelector<HTMLElement>(".warning")!;
    if (!payload.complete) { warning.hidden = false; warning.textContent = payload.warning || "This series is incomplete."; }
    const container = this.root.querySelector<HTMLElement>(".chart")!;
    const dark = document.body.dataset.theme === "dark";
    this.chart = this.chartFactory(container, {
      autoSize: false, attributionLogo: true,
      layout: { background: { type: ColorType.Solid, color: dark ? "#111827" : "#ffffff" }, textColor: dark ? "#d1d5db" : "#374151" },
      grid: { vertLines: { color: dark ? "#263244" : "#e5e7eb" }, horzLines: { color: dark ? "#263244" : "#e5e7eb" } },
      handleScroll: true, handleScale: true,
    });
    this.priceSeries = payload.chart_type === "line"
      ? this.chart.addSeries(LineSeries, { color: "#2563eb", lineWidth: 2 })
      : this.chart.addSeries(CandlestickSeries, { upColor: "#16a34a", downColor: "#dc2626", wickUpColor: "#16a34a", wickDownColor: "#dc2626", borderVisible: false });
    if (payload.chart_type === "line") this.priceSeries.setData(uniqueCandles.map(({time, close}) => ({time, value: close})));
    else this.priceSeries.setData(uniqueCandles.map(({time, open, high, low, close}) => ({time, open, high, low, close})));

    if (payload.show_volume && uniqueCandles.some((item) => item.volume !== undefined)) {
      const volume = this.chart.addSeries(HistogramSeries, { priceScaleId: "", priceFormat: { type: "volume" } });
      volume.priceScale().applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
      volume.setData(uniqueCandles.filter((item) => item.volume !== undefined).map((item) => ({ time: item.time, value: item.volume!, color: item.close >= item.open ? "#16a34a66" : "#dc262666" })));
    }
    for (const overlay of payload.horizontal_overlays || []) {
      if (finite(overlay.price)) this.priceSeries.createPriceLine({ price: overlay.price, title: overlay.label, color: "#8b5cf6", lineStyle: lineStyle(overlay.line_style), lineWidth: overlay.line_width, axisLabelVisible: true });
    }
    for (const overlay of payload.line_overlays || []) {
      const points = uniqueByTime(overlay.points.flatMap((point) => { const time = toUtcTimestamp(point.timestamp); return time && finite(point.value) ? [{time, value: point.value}] : []; }));
      if (points.length) this.chart.addSeries(LineSeries, { title: overlay.label, color: "#f59e0b", lineStyle: lineStyle(overlay.line_style), lineWidth: overlay.line_width, priceLineVisible: false }).setData(points);
    }
    const markerData = (payload.markers || []).flatMap((marker) => { const time = toUtcTimestamp(marker.timestamp); return time ? [{ time, position: marker.position === "above" ? "aboveBar" as const : "belowBar" as const, shape: ({arrow_up:"arrowUp",arrow_down:"arrowDown",circle:"circle",square:"square"} as const)[marker.shape], color: marker.position === "above" ? "#dc2626" : "#16a34a", text: marker.label }] : []; });
    if (markerData.length) this.markerFactory(this.priceSeries, markerData);
    const legend = this.root.querySelector<HTMLElement>(".legend")!;
    this.chart.subscribeCrosshairMove((param) => {
      const data = param.seriesData.get(this.priceSeries!);
      const timestamp = typeof param.time === "number" ? new Date(param.time * 1000).toISOString() : String(param.time ?? "");
      if (data && "close" in data) legend.textContent = `${timestamp} | O ${data.open} H ${data.high} L ${data.low} C ${data.close}`;
      else if (data && "value" in data) legend.textContent = `${timestamp} | Close ${data.value}`;
    });
    this.chart.timeScale().fitContent();
    this.observer = new ResizeObserver((entries) => { const box = entries[0]?.contentRect; if (box && box.width > 0) this.chart?.resize(Math.floor(box.width), Math.floor(box.height)); });
    this.observer.observe(container);
  }

  applyTheme(theme: string): void {
    const dark = theme === "dark";
    document.body.dataset.theme = dark ? "dark" : "light";
    this.chart?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: dark ? "#111827" : "#ffffff" }, textColor: dark ? "#d1d5db" : "#374151" },
      grid: { vertLines: { color: dark ? "#263244" : "#e5e7eb" }, horzLines: { color: dark ? "#263244" : "#e5e7eb" } },
    });
  }
  showError(message: string): void { this.destroyChart(); this.root.innerHTML = `<div class="state error" role="alert"></div>`; this.root.firstElementChild!.textContent = message; }
  destroy(): void { this.destroyChart(); }
  private destroyChart(): void { this.observer?.disconnect(); this.observer = null; this.chart?.remove(); this.chart = null; this.priceSeries = null; }
}
