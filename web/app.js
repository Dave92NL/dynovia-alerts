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
  // The index into state.matches travels with the row: the list is filtered
  // and reversed, so its own position means nothing to anyone else.
  const played = state.matches
    .map((m, index) => ({ m, index }))
    .filter(({ m }) => m.status === "finished")
    .reverse();
  const box = $("results");
  box.innerHTML = "";
  if (!played.length) {
    box.innerHTML = '<div class="card empty">Jeszcze nic nie rozegrano.</div>';
    return;
  }
  const card = el('<div class="card results-card"></div>');
  for (const { m, index } of played) {
    const ours = m.atHome ? m.homeScore : m.awayScore;
    const theirs = m.atHome ? m.awayScore : m.homeScore;
    const result = ours > theirs ? "win" : ours < theirs ? "loss" : "draw";
    card.append(
      el(`<div class="match" role="button" tabindex="0" data-match="${index}">
        <div class="date">${esc(shortDate(m.date))}</div>
        <div class="teams">${
          m.atHome
            ? `<em>${esc(m.home)}</em> – ${esc(m.away)}`
            : `${esc(m.home)} – <em>${esc(m.away)}</em>`
        }</div>
        <div class="score ${result}">${m.homeScore}–${m.awayScore}</div>
        <div class="chevron">›</div>
      </div>`)
    );
    if (m.scorers.length) {
      const list = m.scorers
        .map((g) => esc(g.player) + (g.minute ? ` ${g.minute}'` : ""))
        .join(" · ");
      card.append(el(`<div class="goals">⚽ ${list}</div>`));
    }
  }
  card.addEventListener("click", (event) => {
    const row = event.target.closest("[data-match]");
    if (row) openMatch(Number(row.dataset.match));
  });
  box.append(card);
}

/* --- jeden mecz ----------------------------------------------------------- */

/* Pushed onto history so the iOS back swipe leaves the match rather than the
   app. Without it the gesture closes a standalone PWA outright. */
function openMatch(index) {
  history.pushState({ match: index }, "");
  renderMatch(index);
}

window.addEventListener("popstate", () => {
  if (!$("results").hidden) renderResults();
});

const CARD_MARK = { yellow: "🟨", second_yellow: "🟨🟥", red: "🟥" };
const FULL_TIME = 90;

function timeline(match) {
  /* One list, both teams, in the order it happened. Ours and theirs come from
     different places on purpose: our goals are picked from a single trusted
     source, theirs exist only where a protocol was imported. */
  const events = [
    ...match.scorers.map((g) => ({ minute: g.minute, mark: "⚽", who: g.player, ours: true })),
    ...match.theirGoals.map((g) => ({ minute: g.minute, mark: "⚽", who: g.player, ours: false })),
    ...match.cards.map((c) => ({
      minute: c.minute, mark: CARD_MARK[c.color] || "🟨", who: c.name, ours: c.ours,
    })),
  ];
  for (const p of match.lineup) {
    if (p.minuteIn) events.push({ minute: p.minuteIn, mark: "⬆️", who: p.name, ours: p.ours });
    if (p.minuteOut && p.minuteOut < FULL_TIME) {
      events.push({ minute: p.minuteOut, mark: "⬇️", who: p.name, ours: p.ours });
    }
  }
  return events.sort((a, b) => (a.minute || 0) - (b.minute || 0));
}

function squadBlock(match, ours) {
  const side = match.lineup.filter((p) => p.ours === ours);
  const team = ours === match.atHome ? match.home : match.away;
  if (!side.length) return "";
  const line = (p) =>
    `<li>${esc(p.name)}${
      p.minutes && !p.started ? ` <span class="min">od ${p.minuteIn}'</span>` : ""
    }${
      p.minutes && p.minuteOut < FULL_TIME ? ` <span class="min">do ${p.minuteOut}'</span>` : ""
    }</li>`;
  const starters = side.filter((p) => p.started);
  const subs = side.filter((p) => !p.started);
  return `<div class="squad">
    <h3>${esc(team)}</h3>
    <ul>${starters.map(line).join("")}</ul>
    ${subs.length ? `<p class="tag">Weszli z ławki</p><ul>${subs.map(line).join("")}</ul>` : ""}
  </div>`;
}

function renderMatch(index) {
  const m = state.matches[index];
  const box = $("results");
  const events = timeline(m);
  const when = `${DAY[kickoff(m).getDay()]}, ${shortDate(m.date)}`;

  box.innerHTML = `
    <button class="back" id="backToResults">‹ Wyniki</button>
    <div class="card match-hero">
      <div class="hero-top">
        <span class="tag">${m.round ? "kolejka " + m.round + " · " : ""}${esc(m.competition)}</span>
        <div class="scoreline">${esc(m.home)} <b>${m.homeScore}–${m.awayScore}</b> ${esc(m.away)}</div>
        <div class="when">${when}</div>
      </div>
    </div>
    ${
      events.length
        ? `<div class="section-heading"><h2>Przebieg</h2></div>
           <div class="card timeline">${events
             .map(
               (e) => `<div class="event${e.ours ? "" : " theirs"}">
                 <b>${e.minute ? e.minute + "'" : "–"}</b>
                 <span class="mark">${e.mark}</span>
                 <span>${esc(e.who)}</span>
               </div>`
             )
             .join("")}</div>`
        : ""
    }
    ${
      m.lineup.length
        ? `<div class="section-heading"><h2>Składy</h2></div>
           <div class="card squads">${squadBlock(m, true)}${squadBlock(m, false)}</div>
           ${
             m.lineup.some((p) => p.minutes)
               ? ""
               : '<footer>Bez minut — te są tylko w protokole PZPN.</footer>'
           }`
        : '<div class="card empty">Składów nie ma.<br>Wyślij protokół PZPN — jak, sprawdzisz w Źródłach.</div>'
    }`;
  $("backToResults").addEventListener("click", () => history.back());
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

/* Protokołu PZPN nie da się pobrać - patrz protokol.py - ale Safari potrafi
   zapisać wyrenderowaną stronę. "Kompletna witryna" to .webarchive, w ktorym
   siedzi DOM taki, jaki widac na ekranie, a nie pusta skorupa, ktora przyslal
   serwer. Sprawdzone: 15 wystepow, 3 bramki, 3 kartki z meczu z Gromem.

   Byl tu wczesniej Skrot iOS wyciagajacy outerHTML. Nie przeszedl: 657 kB nie
   miesci sie w granicy miedzy procesami Safari i Skrotow. Trzy tapniecia
   wygrywaja z nim w kazdy mozliwy sposob. */
function protocolHelp() {
  return `<div class="card help">
    <span class="tag">Protokół PZPN z telefonu</span>
    <p><b>Tylko Safari.</b> Inne przeglądarki na iOS nie mają tej opcji.</p>
    <ol>
      <li>Otwórz stronę meczu na laczynaspilka.pl</li>
      <li><b>Udostępnij</b> → <b>Opcje</b> u góry → zaznacz
        <b>Kompletna witryna</b> → Gotowe</li>
      <li>Wybierz <b>Telegram</b> → czat z botem → wyślij</li>
    </ol>
    <p>Bot odpisze, co zaimportował: składy, minuty gry i kartki. Ten sam plik
    możesz wysłać drugi raz, nic się nie zdubluje.</p>
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
