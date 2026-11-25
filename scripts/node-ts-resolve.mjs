// For node's own test runner on the app's TypeScript (node --experimental-strip-types): the app's imports have no
// extension (Vite resolves them), node's ESM wants one. This tries "<spec>.ts" when a relative import isn't found.
//   node --import ./scripts/node-ts-resolve.mjs --experimental-strip-types --test <file>.test.ts
import { register } from "node:module";

register("data:text/javascript," + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  try {
    return await next(spec, ctx);
  } catch (e) {
    if (spec.startsWith(".") && !/\\.[cm]?[jt]sx?$/.test(spec)) return next(spec + ".ts", ctx);
    throw e;
  }
}`));
