import fs from "node:fs/promises";
import { Workbook } from "file:///C:/Users/WenboZhao/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const csvPath = new URL("../../data/capture_20260722_171634.csv", import.meta.url);
const csvText = await fs.readFile(csvPath, "utf8");
const workbook = await Workbook.fromCSV(csvText, { sheetName: "capture" });
const overview = await workbook.inspect({
  kind: "workbook,sheet,region",
  sheetId: "capture",
  range: "A1:E8",
  maxChars: 5000,
  tableMaxRows: 8,
  tableMaxCols: 5,
});
console.log(overview.ndjson);
process.exit(0);
