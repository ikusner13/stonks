import createClient from "openapi-fetch";
import type { paths } from "./schema";

// baseUrl "/": requests go through the same-origin Worker, which proxies
// /api/* to the FastAPI backend (see src/worker.ts).
export const client = createClient<paths>({ baseUrl: "/" });
