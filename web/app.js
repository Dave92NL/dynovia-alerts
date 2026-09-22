"use strict";

/* Dynovia Alerts - the whole frontend.
 *
 * It reads data/*.json and nothing else. No source is ever contacted from
 * here, and no decision is made here either: which source to believe and who
 * a name refers to was settled before the numbers were written out.
 *
 * Dates arrive as local Warsaw wall-clock strings, which is also the phone's
 * clock, so `new Date("2026-09-27T14:00")` is the right instant.
 */

const DATA = ["meta", "matches", "stats", "table", "sources"];
const state = {};

const $ = (id) => document.getElementById(id);
const el = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]
  );

const DAY = ["niedziela", "poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota"];

function kickoff(match) {
  if (!match.date) return null;
  return new Date(`${match.date}T${match.time || "00:00"}:00`);
}

function shortDate(iso) {
  const [, m, d] = iso.split("-");
  return `${d}.${m}`;
}

/* --- ekrany ------------------------------------------------------------- */

function renderNext() {
  const now = Date.now();
  const upcoming = state.matches
    .filter((m) => m.status !== "finished" && m.date)
    .map((m) => ({ m, at: kickoff(m) }))
    .filter(({ at }) => at && at.getTime() > now - 3 * 3600e3)
    .sort((a, b) => a.at - b.at)[0];

  if (!upcoming) {
    $("next").innerHTML = '<div class="card empty">Brak zaplanowanych meczów.</div>';
    return;
  }

  const { m, at } = upcoming;
  const opponent = m.atHome ? m.away : m.home;
  const where = m.atHome ? "u siebie" : "na wyjeździe";
  $("next").innerHTML = "";
  $("next").append(
    el(`<div class="card next match-hero">
      <div class="hero-top">
        <span class="tag">Najbliższy mecz · ${esc(where)}</span>
        <div class="opponent">${esc(opponent)}</div>
        <div class="when">${DAY[at.getDay()]}, ${shortDate(m.date)}${
          m.time ? " o " + esc(m.time) : ""
        }${m.round ? " · kolejka " + m.round : ""}</div>
      </div>
      <div class="countdown" id="cd"></div>
    </div>`)
  );
  tick(at);
}

let timer = null;
function tick(at) {
  clearInterval(timer);
  const paint = () => {
    const left = at - Date.now();
    const box = $("cd");
    if (!box) return clearInterval(timer);
    if (left <= 0) {
      box.innerHTML = '<div><b>⚽</b><span>trwa</span></div>';
      return;
    }
    const d = Math.floor(left / 86400e3);
    const h = Math.floor((left % 86400e3) / 3600e3);
    const min = Math.floor((left % 3600e3) / 60e3);
    box.innerHTML =
      `<div><b>${d}</b><span>dni</span></div>` +
      `<div><b>${h}</b><span>godz.</span></div>` +
      `<div><b>${min}</b><span>min</span></div>`;
  };
  paint();
  timer = setInterval(paint, 30000);
}

function renderResults() {
  const played = state.matches.filter((m) => m.status === "finished").reverse();
  const box = $("results");
  box.innerHTML = "";
  if (!played.length) {
    box.innerHTML = '<div class="card empty">Jeszcze nic nie rozegrano.</div>';
    return;
  }
  const card = el('<div class="card results-card"></div>');
  for (const m of played) {
    const ours = m.atHome ? m.homeScore : m.awayScore;
    const theirs = m.atHome ? m.awayScore : m.homeScore;
    const result = ours > theirs ? "win" : ours < theirs ? "loss" : "draw";
    card.append(
      el(`<div class="match">
        <div class="date">${esc(shortDate(m.date))}</div>
        <div class="teams">${
          m.atHome
            ? `<em>${esc(m.home)}</em> – ${esc(m.away)}`
            : `${esc(m.home)} – <em>${esc(m.away)}</em>`
        }</div>
        <div class="score ${result}">${m.homeScore}–${m.awayScore}</div>
      </div>`)
    );
    if (m.scorers.length) {
      const list = m.scorers
        .map((g) => esc(g.player) + (g.minute ? ` ${g.minute}'` : ""))
        .join(" · ");
      card.append(el(`<div class="goals">⚽ ${list}</div>`));
    }
  }
  box.append(card);
}

function renderStats() {
  const rows = state.stats;
  const box = $("stats");
  if (!rows.length) {
    box.innerHTML = '<div class="card empty">Brak danych o zawodnikach.</div>';
    return;
  }
  const body = rows
    .slice()
    .sort((a, b) => b.goals - a.goals || b.played - a.played || a.name.localeCompare(b.name, "pl"))
    .map(
      (p) => `<tr>
        <td class="name">${esc(p.name)}</td>
        <td class="num">${p.played}</td>
        <td class="num">${p.minutes || "–"}</td>
        <td class="num">${p.goals}</td>
        <td class="num">${p.assists}</td>
        <td class="num">${p.yellow || ""}${p.red ? " 🟥" + p.red : ""}</td>
      </tr>`
    )
    .join("");
  const totalGoals = rows.reduce((sum, p) => sum + p.goals, 0);
  const totalAssists = rows.reduce((sum, p) => sum + p.assists, 0);
  const topScorer = rows.slice().sort((a, b) => b.goals - a.goals)[0];
  box.innerHTML = `<div class="section-heading"><h2>Statystyki zespołu</h2><span>sezon</span></div>
    <div class="stat-summary">
      <div><b>${totalGoals}</b><span>gole</span></div>
      <div><b>${totalAssists}</b><span>asysty</span></div>
      <div><b>${topScorer ? esc(topScorer.name.split(" ")[0]) : "–"}</b><span>lider strzelców</span></div>
    </div>
    <div class="card table-card"><table>
    <thead><tr><th class="name">Zawodnik</th><th class="num">M</th><th class="num">Min</th>
    <th class="num">G</th><th class="num">A</th><th class="num">🟨</th></tr></thead>
    <tbody>${body}</tbody></table></div>
    <footer>Minuty liczone tylko z zaimportowanych protokołów PZPN.</footer>`;
}

function renderTable() {
  const rows = state.table;
  const box = $("table");
  if (!rows.length) {
    box.innerHTML = '<div class="card empty">Tabela jeszcze nie pobrana.</div>';
    return;
  }
  const body = rows
    .map(
      (r) => `<tr class="${r.us ? "us" : ""}">
        <td class="num">${r.position}</td>
        <td class="name">${esc(r.team)}</td>
        <td class="num">${r.played}</td>
        <td class="num">${r.points}</td>
        <td class="num">${r.goalDifference > 0 ? "+" : ""}${r.goalDifference}</td>
      </tr>`
    )
    .join("");
  const ours = rows.find((r) => r.us);
  box.innerHTML = `<div class="section-heading"><h2>Tabela ligowa</h2>${ours ? `<span>${ours.position}. miejsce</span>` : ""}</div>
    <div class="card table-card"><table>
    <thead><tr><th class="num">#</th><th class="name">Drużyna</th><th class="num">M</th>
    <th class="num">Pkt</th><th class="num">+/-</th></tr></thead>
    <tbody>${body}</tbody></table></div>`;
}

function renderSources() {
  const body = state.sources
    .map(
      (s) => `<tr>
        <td class="name">${esc(s.source)}${s.failures ? ` ⚠️×${s.failures}` : ""}</td>
        <td class="num">${s.matches}</td>
        <td class="num">${esc(s.last ? s.last.slice(5, 16).replace("T", " ") : "–")}</td>
      </tr>`
    )
    .join("");
  $("sources").innerHTML = `<div class="card"><table>
    <thead><tr><th class="name">Źródło</th><th class="num">Mecze</th><th class="num">Ostatnio</th></tr></thead>
    <tbody>${body}</tbody></table></div>
    <div class="card" id="pushCard"></div>
    ${protocolHelp()}`;
  renderPush();
}

/* Protokołu PZPN nie da się pobrać - patrz protokol.py. Z telefonu zostaje
   Skrót, bo Safari zapisuje pustą skorupę Angulara zamiast wyrenderowanej
   strony, a akcja "Uruchom JavaScript na stronie" oddaje to, co widać. */
function protocolHelp() {
  return `<div class="card help">
    <span class="tag">Protokół PZPN z telefonu</span>
    <p><b>Tylko Safari.</b> Chrome i Firefox na iOS przekazują do arkusza
    Udostępnij sam adres, nie stronę, więc Skrót nie ma czego odczytać.</p>
    <p>Skrót budujesz raz, trzy akcje:</p>
    <ol>
      <li>Uruchom JavaScript na stronie:
        <code>return document.documentElement.outerHTML</code></li>
      <li>Ustaw nazwę: <code>protokol.html</code></li>
      <li>Udostępnij → Telegram → czat z botem</li>
    </ol>
    <p>Potem: strona meczu na laczynaspilka.pl w Safari → Udostępnij → Skrót →
    wyślij. Bot odpisze, co zaimportował, albo dlaczego nie.</p>
  </div>`;
}

/* --- powiadomienia push -------------------------------------------------- */

function renderPush() {
  const card = $("pushCard");
  if (!card) return;
  const standalone =
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;

  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    card.innerHTML =
      '<span class="tag">Powiadomienia</span><p>Ta przeglądarka ich nie obsługuje. Telegram działa zawsze.</p>';
    return;
  }
  if (!standalone) {
    card.innerHTML =
      '<span class="tag">Powiadomienia</span><p>Na iPhonie push działa dopiero po dodaniu strony do ekranu głównego: <b>Udostępnij → Dodaj do ekranu początkowego</b>, potem otwórz z ikony i wróć tutaj.</p>';
    return;
  }
  card.innerHTML =
    '<span class="tag">Powiadomienia</span><p>Zgoda musi wyjść po kliknięciu - inaczej iOS ją odrzuci.</p>' +
    '<button class="push" id="enablePush">Włącz powiadomienia</button><div id="subOut"></div>';
  $("enablePush").addEventListener("click", subscribe);
}

/* Safari wants a BufferSource here, not the base64url string that Chrome
   accepts, and iOS is the whole point of this app. */
function urlBase64ToUint8Array(value) {
  const padded = (value + "=".repeat((4 - (value.length % 4)) % 4))
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = atob(padded);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function subscribe() {
  const button = $("enablePush");
  button.disabled = true;
  button.textContent = "Czekam na zgodę…";
  try {
    if ((await Notification.requestPermission()) !== "granted") {
      button.textContent = "Odmówiono zgody";
      return;
    }
    const meta = await fetch("data/meta.json").then((r) => r.json());
    if (!meta.vapidPublicKey) {
      button.textContent = "Brak klucza VAPID - najpierw ustaw sekrety";
      return;
    }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(meta.vapidPublicKey),
    });
    button.textContent = "Gotowe - skopiuj poniższe";
    $("subOut").innerHTML =
      '<pre class="sub">' + esc(JSON.stringify(sub.toJSON())) + "</pre>";
  } catch (err) {
    button.disabled = false;
    button.textContent = "Nie udało się - spróbuj ponownie";
    console.error(err);
  }
}

/* --- start --------------------------------------------------------------- */

const RENDER = {
  next: renderNext,
  results: renderResults,
  stats: renderStats,
  table: renderTable,
  sources: renderSources,
};

$("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-tab]");
  if (!button) return;
  for (const b of $("tabs").children) b.setAttribute("aria-current", b === button);
  for (const name of Object.keys(RENDER)) $(name).hidden = name !== button.dataset.tab;
  RENDER[button.dataset.tab]();
});

async function load() {
  const loaded = await Promise.all(
    DATA.map((name) => fetch(`data/${name}.json`).then((r) => r.json()))
  );
  DATA.forEach((name, i) => (state[name] = loaded[i]));

  $("season").textContent = `${state.meta.season} · Klasa A`;
  const when = new Date(state.meta.generatedAt);
  $("updated").textContent = `Zaktualizowano ${when.toLocaleString("pl-PL", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  })}`;
  renderNext();
}

load().catch((err) => {
  $("next").innerHTML =
    '<div class="card empty">Nie udało się wczytać danych.<br>Sprawdź połączenie.</div>';
  console.error(err);
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(console.error);
}
