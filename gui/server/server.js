// .config/opencode/AGENTS.md APPLIED
// NutrientConflictsEvaluation Project AGENTS.md APPLIED
// Fastify edge server: serves the React production build and proxies the API.
import path from "node:path";
import { fileURLToPath } from "node:url";
import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import fastifyHttpProxy from "@fastify/http-proxy";

const here = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 5173);
const host = process.env.HOST || "127.0.0.1";
const apiBase = process.env.API_BASE_URL || "http://127.0.0.1:8077";
const apiPrefix = process.env.API_PREFIX || "/api/v1";
const dist = process.env.GUI_DIST_DIR || path.resolve(here, "..", "dist");

const app = Fastify({ logger: true });

// The React bundle calls /api/v1; also honour the configured prefix so changing
// api.prefix in application.properties does not require a rebuild.
const prefixes = new Set([apiPrefix, "/api/v1"]);
for (const prefix of prefixes) {
  await app.register(fastifyHttpProxy, {
    upstream: apiBase,
    prefix,
    rewritePrefix: "",
    http2: false,
  });
}

await app.register(fastifyStatic, {
  root: dist,
  prefix: "/",
  wildcard: false,
});

app.setNotFoundHandler((request, reply) => {
  if (request.url.startsWith(apiPrefix) || request.url.startsWith("/api/")) {
    reply.code(404).send({ detail: "API route not found" });
    return;
  }
  reply.sendFile("index.html");
});

await app.listen({ port, host });
app.log.info(`GUI served at http://${host}:${port} (API proxy -> ${apiBase})`);
