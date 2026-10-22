// Build the assembly-app operator CLIs into self-contained ESM files in bin/ — the MCP runs these, so
// nobody using the skills needs an assembly-app checkout, pnpm or node_modules. Only the build
// machine needs the checkout (ASSEMBLY_APP_DIR, else ../../assembly-app). Every dependency is bundled
// except pdf.js + @napi-rs/canvas (pdf2md only: pdf.js loads its own files at runtime and canvas is a
// native module) — those install from this folder's package.json (`npm ci --omit=dev`).
//
//   cd cli && npm ci && npm run build

import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const app = resolve(process.env.ASSEMBLY_APP_DIR ?? join(here, "..", "..", "assembly-app"));
const scripts = join(app, "apps", "frontend", "scripts");
if (!existsSync(scripts)) throw new Error(`no assembly-app checkout at ${app} — set ASSEMBLY_APP_DIR`);

const ENTRIES = [
  "upload-issues",
  "upload-renders",
  "upload-product",
  "upload-dwg-geomap",
  "upload-artifact",
  "upload-panomap",
  "pdf2md",
];

for (const name of ENTRIES) {
  await build({
    entryPoints: [join(scripts, `${name}.ts`)],
    outfile: join(here, "bin", `${name}.mjs`),
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node22",
    external: ["pdfjs-dist", "pdfjs-dist/*", "@napi-rs/canvas"],
    // Bundled CommonJS deps still call require() — give the ESM output one.
    banner: { js: "import { createRequire as __assemblyCreateRequire } from 'node:module'; const require = __assemblyCreateRequire(import.meta.url);" },
    legalComments: "none",
    logLevel: "warning",
  });
  console.log(`built bin/${name}.mjs`);
}

// Record exactly which app code these were built from.
const git = (...a) => execFileSync("git", ["-C", app, ...a], { encoding: "utf8" }).trim();
const source = { commit: git("rev-parse", "HEAD"), branch: git("rev-parse", "--abbrev-ref", "HEAD"), builtAt: new Date().toISOString() };
writeFileSync(join(here, "bin", "SOURCE.json"), `${JSON.stringify(source, null, 2)}\n`);
console.log(`source ${source.branch}@${source.commit.slice(0, 7)}`);
