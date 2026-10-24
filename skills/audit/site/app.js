(() => {
  const A = window.AUDIT;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- projects and colours ----------
  const NAMES = { "medtech-second-brain": "medtech", "bts-transenergo-epl-server": "bts-transenergo", "": "без проекта" };
  const FIXED = {
    "medtech-second-brain": "#5aa2ff", "self": "#ff6fa3", "gamedev": "#ffb347", "dont-forget": "#a99bff",
    "bts": "#4fd1a1", "bts-transenergo-epl-server": "#3cc6c6", "robocat": "#c48cff", "catcraft": "#f2cf4a",
    "catcraft-wiki": "#c9b25e", "multi": "#ff8a65", "p-a": "#7ee07e", "infra": "#93a7c4", "": "#7c8299",
  };
  const SPARE = ["#e57373", "#64b5f6", "#aed581", "#ffd54f", "#ba68c8", "#4db6ac", "#f48fb1", "#90caf9"];
  const counts = {};
  A.notes.forEach(n => { counts[n.p] = (counts[n.p] || 0) + 1; });
  const colorOf = {};
  let spare = 0;
  Object.keys(counts).sort((a, b) => counts[b] - counts[a]).forEach(p => {
    colorOf[p] = FIXED[p] || SPARE[spare++ % SPARE.length];
  });
  const nameOf = p => NAMES[p] ?? p;
  const esc = s => s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const dot = p => `<span class="dot" style="background:${colorOf[p]};color:${colorOf[p]}"></span>`;

  // ---------- one renderer for both canvases ----------
  const ease = t => 1 - Math.pow(1 - Math.min(Math.max(t, 0), 1), 3);

  class MapView {
    constructor(canvas, { offsetX = 0, fill = 0.88, intro = false, constellation = false }) {
      this.c = canvas; this.ctx = canvas.getContext("2d");
      this.offsetX = offsetX; this.fill = fill; this.constellation = constellation;
      // A finding can name a note the map has no place for (died, or no vector): it stays invisible.
      this.pts = A.notes.map((n, i) => {
        const off = n.x == null;
        if (off) n = { ...n, x: 0.5, y: 0.5 };
        const ang = Math.random() * Math.PI * 2, rad = Math.random() * 0.04;
        return {
          i, off, x: n.x, y: n.y, col: colorOf[n.p], p: n.p, t: n.t, god: n.k === "godnote",
          sx: 0.5 + Math.cos(ang) * rad, sy: 0.5 + Math.sin(ang) * rad,
          delay: intro && !reduced ? Math.hypot(n.x - 0.5, n.y - 0.5) * 1100 + Math.random() * 250 : -1e9,
          a: 0, r: 0, ta: 0.85, tr: n.k === "godnote" ? 3.2 : 2.1, tw: Math.random() * 6.28,
        };
      });
      this.cam = { cx: 0.5, cy: 0.5, s: 1 }; this.tcam = { ...this.cam };
      this.overlay = null; this.focusAt = 0; this.start = performance.now();
      if (constellation) this.edges = this.nearEdges();
      new ResizeObserver(() => this.resize()).observe(canvas);
      this.resize();
    }
    nearEdges() {
      const out = [], P = this.pts;
      for (let i = 0; i < P.length; i++) {
        if (P[i].off) continue;
        let best = -1, bd = 0.0006;
        for (let j = 0; j < P.length; j++) {
          if (i === j || P[j].off) continue;
          const d = (P[i].x - P[j].x) ** 2 + (P[i].y - P[j].y) ** 2;
          if (d < bd) { bd = d; best = j; }
        }
        if (best > i) out.push([i, best]);
      }
      return out;
    }
    resize() {
      const r = this.c.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
      this.w = r.width; this.h = r.height;
      this.c.width = Math.round(r.width * dpr); this.c.height = Math.round(r.height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    toScreen(x, y, cam = this.cam) {
      const size = Math.min(this.w, this.h) * this.fill * cam.s;
      return [(x - cam.cx) * size + this.w / 2 + this.offsetX * this.w, (y - cam.cy) * size + this.h / 2];
    }
    // ----- states -----
    setAll(a, r) { this.pts.forEach(p => { p.ta = a; p.tr = p.god ? r + 1.1 : r; }); }
    overview() {
      this.setAll(0.85, 2.1); this.tcam = { cx: 0.5, cy: 0.5, s: 1 }; this.overlay = null; this.mark();
    }
    focus(ids, overlay, pad = 0.26) {
      this.setAll(0.13, 1.7);
      ids.forEach(i => { this.pts[i].ta = 1; this.pts[i].tr = 4.6; });
      let x0 = 1, x1 = 0, y0 = 1, y1 = 0;
      const on = ids.filter(i => !this.pts[i].off);
      on.forEach(i => { const p = this.pts[i]; x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); });
      // Groups sit tight on the map, so the frame is the group's own span grown by pad, not a fixed margin.
      // A finding whose notes are all off the map has nothing to frame: the whole map stays in view.
      const span = Math.max(Math.max(x1 - x0, y1 - y0) * (1 + pad * 4), 0.08);
      this.tcam = on.length ? { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, s: Math.min(Math.max(1 / span, 1), 10) } : { cx: 0.5, cy: 0.5, s: 1 };
      this.overlay = overlay; this.mark();
    }
    project(p) {
      this.pts.forEach(q => { q.ta = q.p === p ? 1 : 0.1; q.tr = q.p === p ? 3.2 : 1.7; });
      this.saved = this.saved || { overlay: this.overlay, tcam: this.tcam };
      this.overlay = null; this.tcam = { cx: 0.5, cy: 0.5, s: 1 };
    }
    restore(apply) { if (this.saved) { this.saved = null; apply(); } }
    mark() { this.focusAt = performance.now(); }
    // ----- frame -----
    frame(now, dt) {
      const k = reduced ? 1 : 1 - Math.exp(-dt / 220), kc = reduced ? 1 : 1 - Math.exp(-dt / 420);
      for (const key of ["cx", "cy", "s"]) this.cam[key] += (this.tcam[key] - this.cam[key]) * kc;
      const g = this.ctx, el = now - this.start;
      g.clearRect(0, 0, this.w, this.h);

      if (this.edges) {
        const vis = ease((el - 1400) / 1600) * 0.11;
        if (vis > 0) {
          g.strokeStyle = `rgba(170,180,230,${vis})`; g.lineWidth = 0.6; g.beginPath();
          for (const [a, b] of this.edges) {
            const [ax, ay] = this.toScreen(this.pts[a].x, this.pts[a].y), [bx, by] = this.toScreen(this.pts[b].x, this.pts[b].y);
            g.moveTo(ax, ay); g.lineTo(bx, by);
          }
          g.stroke();
        }
      }
      if (this.overlay) this.overlay(g, (now - this.focusAt), now);

      for (const p of this.pts) {
        if (p.off) continue;
        p.a += (p.ta - p.a) * k; p.r += (p.tr - p.r) * k;
        const t = ease((el - p.delay) / 1300);
        if (t <= 0) continue;
        const wx = p.sx + (p.x - p.sx) * t, wy = p.sy + (p.y - p.sy) * t;
        const [x, y] = this.toScreen(wx, wy);
        if (x < -10 || y < -10 || x > this.w + 10 || y > this.h + 10) continue;
        const tw = this.constellation && !reduced ? 0.82 + 0.18 * Math.sin(now / 900 + p.tw) : 1;
        const a = p.a * t * tw;
        if (p.r > 3.5 || p.god) {
          g.globalAlpha = a * 0.22; g.fillStyle = p.col;
          g.beginPath(); g.arc(x, y, p.r * 2.6, 0, 6.283); g.fill();
        }
        g.globalAlpha = a; g.fillStyle = p.col;
        g.beginPath(); g.arc(x, y, p.r, 0, 6.283); g.fill();
      }
      g.globalAlpha = 1;
    }
    nearest(mx, my) {
      let best = null, bd = 144;
      for (const p of this.pts) {
        if (p.off || p.a < 0.3) continue;
        const [x, y] = this.toScreen(p.x, p.y), d = (x - mx) ** 2 + (y - my) ** 2;
        if (d < bd) { bd = d; best = p; }
      }
      return best;
    }
  }

  // ---------- overlay painters ----------
  function label(g, x, y, text, col, align = "left") {
    g.font = '500 12px Onest, system-ui, sans-serif';
    const t = text.length > 46 ? text.slice(0, 45) + "…" : text;
    const w = g.measureText(t).width, pad = 6;
    let lx = align === "left" ? x + 10 : x - 10 - w - pad * 2;
    lx = Math.max(4, Math.min(lx, g.canvas.clientWidth - w - pad * 2 - 4));
    g.globalAlpha = 0.92; g.fillStyle = "rgba(10,13,28,.9)";
    g.beginPath(); g.roundRect(lx, y - 11, w + pad * 2, 22, 6); g.fill();
    g.strokeStyle = col; g.globalAlpha = 0.5; g.lineWidth = 1; g.stroke();
    g.globalAlpha = 1; g.fillStyle = "#eef0f7"; g.fillText(t, lx + pad, y + 4);
  }

  const groupOverlay = (view, g) => {
    const ids = g.members, P = view.pts;
    const cx = ids.reduce((s, i) => s + P[i].x, 0) / ids.length, cy = ids.reduce((s, i) => s + P[i].y, 0) / ids.length;
    return (ctx, since, now) => {
      const [ox, oy] = view.toScreen(cx, cy);
      ids.forEach((i, n) => {
        const t = reduced ? 1 : ease((since - 150 - n * 55) / 700);
        if (t <= 0) return;
        const [x, y] = view.toScreen(P[i].x, P[i].y);
        const grad = ctx.createLinearGradient(ox, oy, x, y);
        grad.addColorStop(0, "rgba(185,168,255,.75)"); grad.addColorStop(1, P[i].col);
        ctx.strokeStyle = grad; ctx.globalAlpha = 0.55; ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + (x - ox) * t, oy + (y - oy) * t); ctx.stroke();
      });
      const pulse = reduced ? 0 : (now / 1400) % 1;
      ctx.globalAlpha = 0.5 * (1 - pulse); ctx.strokeStyle = "#b9a8ff"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(ox, oy, 8 + pulse * 26, 0, 6.283); ctx.stroke();
      ctx.globalAlpha = 1; ctx.fillStyle = "#b9a8ff";
      ctx.beginPath(); ctx.arc(ox, oy, 5, 0, 6.283); ctx.fill();
      if (since > 500 || reduced) label(ctx, ox, oy - 24, g.name, "#b9a8ff");
    };
  };

  const arcOverlay = (view, a, b, { dashed = false, from = "", to = "" } = {}) => {
    const P = view.pts;
    return (ctx, since, now) => {
      if (P[a].off || P[b].off) return;
      const [ax, ay] = view.toScreen(P[a].x, P[a].y), [bx, by] = view.toScreen(P[b].x, P[b].y);
      const mx = (ax + bx) / 2, my = (ay + by) / 2, dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1;
      const bend = Math.min(90, len * 0.45), qx = mx - dy / len * bend, qy = my + dx / len * bend;
      const t = reduced ? 1 : ease((since - 200) / 900);
      const pt = s => [(1 - s) ** 2 * ax + 2 * (1 - s) * s * qx + s * s * bx, (1 - s) ** 2 * ay + 2 * (1 - s) * s * qy + s * s * by];
      const grad = ctx.createLinearGradient(ax, ay, bx, by);
      grad.addColorStop(0, P[a].col); grad.addColorStop(1, P[b].col);
      ctx.strokeStyle = grad; ctx.lineWidth = 2; ctx.globalAlpha = 0.9;
      ctx.setLineDash(dashed ? [5, 6] : []);
      ctx.beginPath(); ctx.moveTo(ax, ay);
      for (let s = 0.02; s <= t + 1e-6; s += 0.02) { const [x, y] = pt(s); ctx.lineTo(x, y); }
      ctx.stroke(); ctx.setLineDash([]);
      if (t >= 1 && !reduced) {
        const s = (now / 1600) % 1, [x, y] = pt(s);
        ctx.globalAlpha = 1; ctx.fillStyle = "#fff";
        ctx.shadowColor = "#b9a8ff"; ctx.shadowBlur = 14;
        ctx.beginPath(); ctx.arc(x, y, 3, 0, 6.283); ctx.fill(); ctx.shadowBlur = 0;
      }
      if (since > 600 || reduced) {
        const left = ax <= bx;
        label(ctx, ax, ay, from || nameOf(P[a].p), P[a].col, left ? "right" : "left");
        label(ctx, bx, by, to || nameOf(P[b].p), P[b].col, left ? "left" : "right");
      }
      ctx.globalAlpha = 1;
    };
  };

  const ORDERED = new Set(["спор", "устарело", "заголовок устарел"]);
  const disputeOverlay = (view, d) => {
    const ordered = d.ordered && ORDERED.has(d.verdict);
    const base = arcOverlay(view, d.old.id, d.new.id, ordered
      ? { dashed: true, from: "было: " + d.old.when, to: "стало: " + d.new.when }
      : { dashed: d.verdict === "дубль" });
    return (ctx, since, now) => {
      base(ctx, since, now);
      if (!ordered || view.pts[d.old.id].off) return;
      const [x, y] = view.toScreen(view.pts[d.old.id].x, view.pts[d.old.id].y), t = reduced ? 1 : ease((since - 700) / 400);
      ctx.strokeStyle = "#ff8f6b"; ctx.lineWidth = 2; ctx.globalAlpha = t;
      ctx.beginPath(); ctx.moveTo(x - 7, y - 7); ctx.lineTo(x + 7, y + 7); ctx.moveTo(x + 7, y - 7); ctx.lineTo(x - 7, y + 7); ctx.stroke();
      ctx.globalAlpha = 1;
    };
  };

  const crossOut = (view, id, text) => (ctx, since) => {
    const p = view.pts[id];
    if (p.off) return;
    const [x, y] = view.toScreen(p.x, p.y), t = reduced ? 1 : ease((since - 300) / 400);
    ctx.strokeStyle = "#ff8f6b"; ctx.lineWidth = 2; ctx.globalAlpha = t;
    ctx.beginPath(); ctx.moveTo(x - 8, y - 8); ctx.lineTo(x + 8, y + 8); ctx.moveTo(x + 8, y - 8); ctx.lineTo(x - 8, y + 8); ctx.stroke();
    ctx.globalAlpha = 1;
    if (since > 500 || reduced) label(ctx, x, y - 20, text, "#ff8f6b");
  };

  // ---------- findings: each one knows how to draw itself as a card, a list row and a map focus ----------
  const plural = (n, one, few, many) => n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many;
  const dots = ids => [...new Set(ids.map(i => A.notes[i].p))].slice(0, 6).map(dot).join("");
  const WARN = ORDERED;
  const mark = key => `<span class="mark" data-key="${key}"><span class="mark-btns"><button type="button" data-v="yes">согласен</button><button type="button" data-v="no">нет</button><button type="button" class="note-toggle" aria-label="комментарий">✎</button></span><textarea class="mark-note" rows="2" hidden></textarea></span>`;

  const groupFinding = g => {
    const projects = new Set(g.members.map(i => A.notes[i].p)).size, shown = g.members.slice(0, 5);
    return {
      card: `<article class="card">
        <div class="meta-row"><span class="count">${g.members.length} ${plural(g.members.length, "карточка", "карточки", "карточек")} · ${projects} ${plural(projects, "проект", "проекта", "проектов")}</span>
        ${g.badge ? `<span class="pill">${esc(g.badge)}</span>` : ""}
        ${g.linked_pairs === 0 ? `<span class="pill warn">друг на друга не ссылаются</span>` : ""}</div>
        <h3>${esc(g.name)}</h3>
        <p class="thesis">${esc(g.thesis)}</p>
        <ul class="members">${shown.map((i, n) => `<li style="transition-delay:${120 + n * 70}ms">${dot(A.notes[i].p)}<span>${esc(A.notes[i].t)}</span></li>`).join("")}
        ${g.members.length > shown.length ? `<li class="more" style="transition-delay:${120 + shown.length * 70}ms">и ещё ${g.members.length - shown.length}</li>` : ""}</ul>
        ${mark(g.key)}
      </article>`,
      row: `<span class="row-dots">${dots(g.members)}</span><span class="row-main"><b>${esc(g.name)}</b><span>${esc(g.thesis)}</span>${mark(g.key)}</span><span class="row-meta">${g.badge ? `<span class="pill">${esc(g.badge)}</span> ` : ""}${g.members.length}</span>`,
      apply: v => v.focus(g.members, groupOverlay(v, g)),
    };
  };
  const disputeFinding = d => {
    const ordered = d.ordered && WARN.has(d.verdict);
    // Each side names its note in full: the reader's fix says "a"/"b", and a verdict can't be checked without the titles.
    const side = (s, cls) => `<div class="ver ${cls}"><span class="when">${s.side ? `<i class="side-tag">${s.side}</i> ` : ""}${esc(s.when)}${s.id == null ? "" : " · " + esc(nameOf(A.notes[s.id].p))}</span>${s.id == null ? "" : `<b class="ver-title">${esc(A.notes[s.id].t)}</b>`}<p>${esc(s.say)}</p></div>`;
    const versions = `<div class="versions">${side(d.old, ordered ? "old" : "")}${side(d.new, "new")}</div>`;
    return {
      card: `<article class="card">
        <div class="meta-row"><span class="pill ${WARN.has(d.verdict) ? "warn" : ""}">${esc(d.verdict)}</span></div>
        <h3>${esc(d.topic)}</h3>
        ${versions}
        <p class="fix"><b>Предложение:</b> ${esc(d.fix)}</p>
        ${mark(d.key)}
      </article>`,
      row: `<span class="row-dots">${dot(A.notes[d.old.id].p)}</span><span class="row-main"><b>${esc(d.topic)}</b><span>${esc(d.fix)}</span><span class="row-more">${versions}</span>${mark(d.key)}</span><span class="row-meta"><span class="pill ${WARN.has(d.verdict) ? "warn" : ""}">${esc(d.verdict)}</span></span>`,
      apply: v => d.new.id == null ? v.focus([d.old.id], crossOut(v, d.old.id, d.verdict === "заголовок устарел" ? "заголовок говорит не то, что поправка" : "устарело, но поиск считает живой"), 0.45) : v.focus([d.old.id, d.new.id], disputeOverlay(v, d), 0.45),
    };
  };

  // ---------- story: a few findings told one by one, then the full list of the chapter ----------
  const steps = [];
  const dropNote = (n, what) => n ? ` · ещё ${n} ${what} агент прочитал и отбросил` : "";
  const chapter = (no, title, text, findings, featured, dropped, droppedWhat) => {
    if (!findings.length) return;
    steps.push({ html: `<div class="chapter"><div class="no">${no}</div><h2>${title}</h2><p>${text}</p></div>`, apply: v => v.overview() });
    findings.slice(0, featured).forEach(f => steps.push({ html: f.card, apply: f.apply }));
    if (findings.length > featured) steps.push({
      list: findings,
      html: `<div class="all"><div class="all-head"><h3>Все ${findings.length}</h3><span class="count">${dropNote(dropped, droppedWhat).replace(/^ · /, "")}</span></div>
        <ol class="rows">${findings.map((f, k) => `<li tabindex="0" data-k="${k}">${f.row}</li>`).join("")}</ol></div>`,
      apply: v => v.overview(),
    });
  };
  const D = A.dropped || {};
  chapter("01", "Просятся в godnote", "Карточки про одно и то же, у которых нет общей страницы-вывода. Название и тезис предлагает агент, решаешь ты.",
    A.godnotes.map(groupFinding), 5, D.group, "групп");
  chapter("02", "Устарели, спорят, повторяются", "Пары, которые агент прочитал целиком. Сверху те, где волт сейчас может подсунуть неверный совет.",
    A.disputes.map(disputeFinding), 4, (D.same || 0) + (D.stale || 0), "пар");

  // ---------- wire up ----------
  document.getElementById("date").textContent = A.date;
  const hero = new MapView(document.getElementById("hero-map"), {
    offsetX: innerWidth > 860 ? 0.2 : 0, fill: innerWidth > 860 ? 0.86 : 1.0, intro: true, constellation: true,
  });
  const map = new MapView(document.getElementById("map"), { fill: 0.86 });
  map.pts.forEach(p => { p.a = p.ta; p.r = p.tr; });
  map.overview();

  const holder = document.getElementById("steps");
  holder.innerHTML = steps.map((s, i) => `<section class="step${s.list ? " list-step" : ""}" data-i="${i}">${s.html}</section>`).join("");
  let current = -1;
  const activate = i => {
    if (i === current) return;
    current = i;
    holder.querySelectorAll(".step").forEach(el => el.classList.toggle("active", +el.dataset.i === i));
    holder.querySelectorAll(".rows li.on").forEach(li => li.classList.remove("on"));
    map.saved = null;
    steps[i].apply(map);
  };
  // The active step is the one whose content is most visible in the reading area (below the sticky map on phones);
  // a step keeps the focus until most of it has left, so a card you are still reading never dims.
  const stepEls = [...holder.querySelectorAll(".step")];
  const mapWrap = document.querySelector(".map-wrap");
  const pick = () => {
    const top = matchMedia("(max-width: 860px)").matches ? mapWrap.getBoundingClientRect().bottom : 0;
    const bottom = innerHeight, view = bottom - top;
    let best = -1, bestSeen = 0, curSeen = 0;
    stepEls.forEach((el, i) => {
      const r = (el.firstElementChild || el).getBoundingClientRect();
      const seen = Math.max(0, Math.min(r.bottom, bottom) - Math.max(r.top, top)) / Math.min(r.height || 1, view);
      if (seen > bestSeen) { bestSeen = seen; best = i; }
      if (i === current) curSeen = seen;
    });
    if (best >= 0 && (current < 0 || curSeen < 0.6)) activate(best);
  };
  let queued = false;
  addEventListener("scroll", () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; pick(); }); } }, { passive: true });
  addEventListener("resize", pick);
  pick();

  // list rows show their finding on the map
  holder.querySelectorAll(".list-step").forEach(section => {
    const findings = steps[+section.dataset.i].list;
    section.querySelectorAll(".rows li").forEach(li => {
      const show = () => {
        activate(+section.dataset.i);
        section.querySelectorAll("li.on").forEach(x => x.classList.remove("on"));
        li.classList.add("on"); map.saved = null;
        findings[+li.dataset.k].apply(map);
      };
      li.addEventListener("mouseenter", show); li.addEventListener("focus", show); li.addEventListener("click", show);
    });
  });

  // decisions: the viewer marks findings and leaves a note (why "no", or a task instead of a rule);
  // Claude reads the `decisions` collection back and acts on it
  const chip = document.getElementById("decided"), chipErr = chip.querySelector("span");
  const decisions = {}, opened = new Set();
  const paint = () => {
    document.querySelectorAll(".mark").forEach(m => {
      const d = decisions[m.dataset.key] || {}, note = m.querySelector(".mark-note");
      m.querySelectorAll("button[data-v]").forEach(b => b.setAttribute("aria-pressed", String(d.v === b.dataset.v)));
      m.querySelector(".note-toggle").setAttribute("aria-pressed", String(!!d.c));
      if (document.activeElement !== note) note.value = d.c || "";
      note.placeholder = d.v === "no" ? "почему" : "комментарий";
      note.hidden = !(d.v === "no" || d.c || opened.has(m.dataset.key));
    });
    const vals = Object.values(decisions), yes = vals.filter(d => d.v === "yes").length, no = vals.filter(d => d.v === "no").length, notes = vals.filter(d => d.c).length;
    chip.querySelector("b").textContent = vals.length ? `${yes} согласен · ${no} нет${notes ? ` · ${notes} ${plural(notes, "комментарий", "комментария", "комментариев")}` : ""}` : "ничего не отмечено";
  };
  (async () => {
    const db = window.claude ? await window.claude.use("db") : null;
    if (!db) return;
    document.body.classList.add("can-mark"); chip.hidden = false;
    const col = db.collection("decisions");
    // Keys whose latest local mark is not saved yet: typed and waiting for the pause, or written and
    // not acknowledged. A snapshot is older than such a mark, so for these keys the local one wins.
    const unsaved = {}, typing = new Set();
    col.onSnapshot(snap => {
      const local = {};
      for (const k in decisions) if (unsaved[k] || typing.has(k)) local[k] = decisions[k];
      for (const k in decisions) delete decisions[k];
      snap.docs.forEach(d => { const x = d.data(); decisions[d.id] = { v: x.v || undefined, c: x.c || "" }; });
      Object.assign(decisions, local);
      paint();
    }, e => { console.warn("decisions subscription stopped", e.code, e.message); chipErr.textContent = "отметки не загрузились, обнови страницу"; });
    // One write at a time per finding: each save waits for the previous one to the same document.
    // The mark is taken when the user acts, not when the write gets its turn.
    const queue = {};
    const save = key => {
      const d = { ...(decisions[key] || {}) };
      unsaved[key] = (unsaved[key] || 0) + 1;
      queue[key] = (queue[key] || Promise.resolve()).then(async () => {
        try {
          if (d.v || d.c) await col.doc(key).set({ v: d.v || null, c: d.c || "", at: new Date().toISOString() });
          else await col.doc(key).delete();
          chipErr.textContent = "";
        } catch (err) {
          console.warn("decision not saved", key, err.code, err.message);
          chipErr.textContent = "не сохранилось, попробуй ещё раз";
        } finally {
          if (!--unsaved[key]) delete unsaved[key];
        }
      });
    };
    document.addEventListener("click", e => {
      const m = e.target.closest(".mark");
      if (!m) return;
      e.stopPropagation();
      const btn = e.target.closest("button"), key = m.dataset.key;
      if (!btn) return;
      const d = decisions[key] = { ...(decisions[key] || {}) };
      if (btn.classList.contains("note-toggle")) {
        opened.has(key) ? opened.delete(key) : opened.add(key);
        paint(); if (!m.querySelector(".mark-note").hidden) m.querySelector(".mark-note").focus();
        return;
      }
      d.v = d.v === btn.dataset.v ? undefined : btn.dataset.v;
      paint(); save(key);
      if (d.v === "no" && !d.c) m.querySelector(".mark-note").focus();
    }, true);
    const timers = {};
    document.addEventListener("input", e => {
      const note = e.target.closest(".mark-note");
      if (!note) return;
      const key = note.parentElement.dataset.key;
      decisions[key] = { ...(decisions[key] || {}), c: note.value };
      document.querySelectorAll(`.mark[data-key="${key}"] .mark-note`).forEach(n => { if (n !== note) n.value = note.value; });
      typing.add(key);
      clearTimeout(timers[key]); timers[key] = setTimeout(() => { save(key); typing.delete(key); paint(); }, 700);
    });
  })();

  // legend: hover a project to light it up
  const legend = document.getElementById("legend");
  Object.keys(counts).sort((a, b) => counts[b] - counts[a]).slice(0, 11).forEach(p => {
    const b = document.createElement("button");
    b.type = "button"; b.innerHTML = `${dot(p)}${esc(nameOf(p))}`;
    const on = () => map.project(p), off = () => map.restore(() => { if (current >= 0) { const c = current; current = -1; activate(c); } else map.overview(); });
    b.addEventListener("mouseenter", on); b.addEventListener("focus", on);
    b.addEventListener("mouseleave", off); b.addEventListener("blur", off);
    legend.appendChild(b);
  });

  // tooltip
  const tip = document.getElementById("tip"), mc = document.getElementById("map");
  mc.addEventListener("mousemove", e => {
    const r = mc.getBoundingClientRect(), p = map.nearest(e.clientX - r.left, e.clientY - r.top);
    if (!p) { tip.hidden = true; return; }
    tip.innerHTML = `<div class="tp">${dot(p.p)}${esc(nameOf(p.p))}${p.god ? " · godnote" : ""}</div>${esc(p.t)}`;
    tip.hidden = false;
    const x = Math.min(e.clientX - r.left + 14, r.width - 290), y = Math.max(e.clientY - r.top - 10 - tip.offsetHeight, 8);
    tip.style.left = x + "px"; tip.style.top = y + "px";
  });
  mc.addEventListener("mouseleave", () => { tip.hidden = true; });

  // stats count-up
  document.querySelectorAll("[data-count]").forEach(el => {
    const target = A.stats[el.dataset.count], t0 = performance.now() + 500;
    const tick = now => {
      const t = reduced ? 1 : ease((now - t0) / 1600);
      el.textContent = Math.round(target * t).toLocaleString("ru-RU");
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  // render loop — the hero stops drawing once it is off screen
  let heroVisible = true;
  new IntersectionObserver(([e]) => { heroVisible = e.isIntersecting; }).observe(document.querySelector(".hero"));
  let last = performance.now();
  const loop = now => {
    const dt = Math.max(0, Math.min(now - last, 64)); last = now;
    if (heroVisible) hero.frame(now, dt);
    map.frame(now, dt);
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
})();
