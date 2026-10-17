import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { use } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";

use([
  SVGRenderer,
  BarChart,
  LineChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
]);

export const chartColors = {
  green: "#173e34",
  greenHover: "#285744",
  faded: "#c9d3c2",
  sage: "#8ea99b",
  muted: "#727970",
  line: "#dddfd5",
  paper: "#f7f6f0",
  heatLow: "#eef1e7",
};

export const tooltipStyle = {
  backgroundColor: "#fbfbf6",
  borderColor: chartColors.line,
  borderWidth: 1,
  padding: [10, 14],
  textStyle: { color: "#243b34", fontFamily: "DM Sans Variable, sans-serif", fontSize: 12 },
  extraCssText: "box-shadow: 0 8px 24px rgba(23, 62, 52, 0.12); border-radius: 6px;",
};

export const axisLabelStyle = {
  color: chartColors.muted,
  fontFamily: "DM Sans Variable, sans-serif",
  fontSize: 11,
};
