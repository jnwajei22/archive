import { App } from "@modelcontextprotocol/ext-apps";
import { MarketChartWidget, type ToolResult } from "./chart";
import "./styles.css";

const root = document.getElementById("app");
if (!root) throw new Error("Market chart root element is missing.");
const widget = new MarketChartWidget(root);
const app = new App({ name: "Agentic Forex Market Chart", version: "1.0.0" }, {});

app.ontoolresult = (result) => widget.renderResult(result as ToolResult);
app.onhostcontextchanged = (context) => { if (context.theme) widget.applyTheme(context.theme); };
app.onteardown = async () => { widget.destroy(); return {}; };

app.connect().then(() => {
  widget.applyTheme(app.getHostContext()?.theme ?? "light");
}).catch(() => widget.showError("Unable to connect the market chart to the host."));
