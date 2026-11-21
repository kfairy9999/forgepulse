import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(root, "playground.html"), "utf8");
writeFileSync(
  join(root, "src", "page.ts"),
  `export const PLAYGROUND_HTML = ${JSON.stringify(html)};\n`,
  "utf8",
);
console.log("wrote demo/src/page.ts");
