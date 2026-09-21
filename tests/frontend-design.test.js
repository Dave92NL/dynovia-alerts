import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../web/index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");

test("mobile design has clear hierarchy for match, statistics, and table", () => {
  assert.match(html, /class="app-shell"/);
  assert.match(html, /\.match-hero/);
  assert.match(html, /\.stat-summary/);
  assert.match(html, /\.table-card/);
  assert.match(app, /match-hero/);
  assert.match(app, /stat-summary/);
  assert.match(app, /table-card/);
});
