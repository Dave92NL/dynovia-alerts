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

test("the sources tab explains how to send a protocol from a phone", () => {
  // Every one of these sank a real attempt at following the instructions, so
  // each is asserted rather than trusted to survive the next edit.
  //
  // Safari is a requirement, not advice - the Shortcuts JS action gets nothing
  // from Chrome or Firefox on iOS.
  assert.match(app, /Tylko Safari/);
  // `return` is rejected outright: the action waits for completion().
  assert.match(app, /completion\(/);
  // An unwired input produces silence with no error, which is unguessable.
  assert.match(app, /Dane wejściowe skrótu/);
  // Without this the Shortcut never appears in Safari at all.
  assert.match(app, /arkuszu\s+udostępniania/);
  // 650 kB will not cross XPC; the page has to travel compressed.
  assert.match(app, /CompressionStream/);
  assert.match(html, /\.help code/);
});
