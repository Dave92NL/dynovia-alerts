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
  // Safari only: no other iOS browser offers this share option at all.
  assert.match(app, /Tylko Safari/);
  // The three taps that actually work. An iOS Shortcut extracting outerHTML
  // stood here first and never got past 657 kB of page; if these strings go,
  // the instructions have drifted back to something unfollowable.
  assert.match(app, /Kompletna witryna/);
  assert.match(app, /Opcje/);
});
