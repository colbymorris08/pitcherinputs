(() => {
  const ALL_TAB = 'ALL';

  const state = {
    catalog: null,
    team: null, // team abbrev or ALL
    kind: null, // pitcher | catcher
    playerId: null,
    playerTeam: null, // owning team when browsing ALL
    hls: null,
  };

  const $ = (id) => document.getElementById(id);

  async function boot() {
    try {
      const res = await fetch(`data/catalog.json?v=${Date.now()}`);
      if (!res.ok) throw new Error(`catalog ${res.status}`);
      state.catalog = await res.json();
    } catch (e) {
      $('meta').textContent = `Catalog missing — run scripts/build_catalog.py (${e.message})`;
      return;
    }
    const c = state.catalog;
    const catcherN = (c.teams || []).reduce((n, t) => n + ((t.catchers || []).length), 0);
    $('meta').textContent = [
      `Updated ${c.updated || '—'}`,
      `${(c.teams || []).length} teams`,
      `${catcherN} catchers`,
      `stream-only (no local mp4)`,
    ].join(' · ');
    renderTeams();
    selectTeam(ALL_TAB);
    $('dock-close').onclick = closeDock;
  }

  function renderTeams() {
    const root = $('team-tabs');
    const allBtn = `<button type="button" data-team="${ALL_TAB}" class="${state.team === ALL_TAB ? 'active' : ''}">ALL<br><span class="muted">League catchers</span></button>`;
    const teamBtns = (state.catalog.teams || []).map((t) =>
      `<button type="button" data-team="${t.abbrev}" class="${t.abbrev === state.team ? 'active' : ''}">${t.abbrev}<br><span class="muted">${t.name}</span></button>`
    ).join('');
    root.innerHTML = allBtn + teamBtns;
    root.querySelectorAll('button').forEach((btn) => {
      btn.onclick = () => selectTeam(btn.dataset.team);
    });
  }

  function teamPack(abbrev) {
    return (state.catalog.teams || []).find((t) => t.abbrev === abbrev) || null;
  }

  function allCatchersGrouped() {
    return (state.catalog.teams || []).map((t) => ({
      abbrev: t.abbrev,
      name: t.name,
      catchers: ((t.catchers || []).slice().sort((a, b) =>
        String(a.last || a.name).localeCompare(String(b.last || b.name))
      )),
    }));
  }

  function selectTeam(abbrev) {
    state.team = abbrev;
    state.kind = null;
    state.playerId = null;
    state.playerTeam = null;
    renderTeams();
    renderLists();
    $('empty').hidden = false;
    $('player-panel').hidden = true;
    closeDock();
    if (abbrev === ALL_TAB) {
      $('empty').textContent = 'Pick a catcher — all league catchers, grouped by team (throw downs to 2B).';
    } else {
      $('empty').textContent = 'Pick a pitcher or catcher.';
    }
  }

  function renderLists() {
    const pitcherSeg = document.querySelector('.rail-seg.pitchers') || document.querySelector('.rail-seg');
    const catcherSeg = document.querySelector('.rail-seg.catchers') || document.querySelectorAll('.rail-seg')[1];

    if (state.team === ALL_TAB) {
      if (pitcherSeg) pitcherSeg.hidden = true;
      if (catcherSeg) {
        catcherSeg.hidden = false;
        const h2 = catcherSeg.querySelector('h2');
        if (h2) h2.innerHTML = `All league catchers <span class="muted">by team · SB / CS 2B</span>`;
      }
      $('catcher-list').innerHTML = allCatchersGrouped().map((g) => {
        const buttons = (g.catchers || []).map((p) => catcherButton(p, g.abbrev)).join('')
          || `<div class="muted">No catchers</div>`;
        return `<div class="team-group">
          <div class="team-group-head">${esc(g.abbrev)} <span class="muted">${esc(g.name || '')}</span></div>
          ${buttons}
        </div>`;
      }).join('') || `<div class="muted">No catchers</div>`;
      $('pitcher-list').innerHTML = '';
    } else {
      if (pitcherSeg) pitcherSeg.hidden = false;
      if (catcherSeg) {
        catcherSeg.hidden = false;
        const h2 = catcherSeg.querySelector('h2');
        if (h2) h2.innerHTML = `Catchers <span class="muted">SB / CS 2B throws</span>`;
      }
      const pack = teamPack(state.team);
      const pitchers = ((pack && pack.pitchers) || []).slice().sort((a, b) =>
        String(a.last || a.name).localeCompare(String(b.last || b.name))
      );
      const catchers = ((pack && pack.catchers) || []).slice().sort((a, b) =>
        String(a.last || a.name).localeCompare(String(b.last || b.name))
      );
      $('pitcher-list').innerHTML = pitchers.map((p) => {
        const n = (p.clips || []).filter((c) => c && c.url).length;
        return `<button type="button" data-kind="pitcher" data-id="${p.id}" data-team="${state.team}" class="${state.kind==='pitcher'&&String(state.playerId)===String(p.id)?'active':''}">
          ${esc(p.name)}<span class="badge">${n}/2</span></button>`;
      }).join('') || `<div class="muted">No pitchers</div>`;
      $('catcher-list').innerHTML = catchers.map((p) => catcherButton(p, state.team)).join('')
        || `<div class="muted">No catchers</div>`;
    }

    document.querySelectorAll('#pitcher-list button, #catcher-list button').forEach((btn) => {
      btn.onclick = () => selectPlayer(btn.dataset.kind, btn.dataset.id, btn.dataset.team);
    });
  }

  function catcherButton(p, teamAbbrev) {
    const n = (p.clips || []).filter((c) => c && c.url).length;
    const active = state.kind === 'catcher'
      && String(state.playerId) === String(p.id)
      && String(state.playerTeam || state.team) === String(teamAbbrev);
    const src = p.source === 'field' ? ' <span class="muted">· field</span>' : '';
    return `<button type="button" data-kind="catcher" data-id="${esc(String(p.id))}" data-team="${esc(teamAbbrev)}" class="${active ? 'active' : ''}">
      <span class="player-name">${esc(p.name)}</span>${src}<span class="badge">${n}</span></button>`;
  }

  function findPlayer(kind, id, teamAbbrev) {
    const pack = teamPack(teamAbbrev || state.team);
    if (!pack) return null;
    const list = kind === 'catcher' ? pack.catchers : pack.pitchers;
    return (list || []).find((p) => String(p.id) === String(id)) || null;
  }

  function selectPlayer(kind, id, teamAbbrev) {
    state.kind = kind;
    state.playerId = id;
    state.playerTeam = teamAbbrev || state.team;
    renderLists();
    const p = findPlayer(kind, id, state.playerTeam);
    if (!p) return;
    $('empty').hidden = true;
    $('player-panel').hidden = false;
    const teamLabel = state.playerTeam || state.team;
    $('player-name').textContent = kind === 'catcher'
      ? `${p.name} · ${teamLabel}`
      : p.name;
    $('player-sub').textContent = kind === 'pitcher'
      ? `${teamLabel} · runner on 1B · one fastball + one offspeed`
      : `${teamLabel} · catcher throw down to 2B (SB / CS) · ${p.name}`;
    const clips = p.clips || [];
    if (kind === 'pitcher') {
      const slots = [
        { key: 'FB', label: 'Fastball · R1' },
        { key: 'OS', label: 'Offspeed · R1' },
      ];
      $('clip-grid').innerHTML = slots.map((s) => {
        const clip = clips.find((c) => c && c.slot === s.key) || null;
        return clipCard(s.label, clip);
      }).join('');
    } else {
      if (!clips.length) {
        $('clip-grid').innerHTML = clipCard(`${p.name} · Throw to 2B`, null);
      } else {
        $('clip-grid').innerHTML = clips.map((c, i) => {
          const title = c.label || `${c.catcher_name || p.name} · Throw 2B · ${c.steal_result || c.result || 'SB/CS'}`;
          return clipCard(title, c);
        }).join('');
      }
    }
    document.querySelectorAll('.clip-card[data-url]').forEach((card) => {
      card.onclick = () => play(card.dataset.url, card.dataset.title, card.dataset.detail);
    });
  }

  function clipCard(title, clip) {
    if (!clip || !clip.url) {
      return `<div class="clip-card missing"><h3>${esc(title)}</h3><p>No Synergy clip found yet.</p></div>`;
    }
    const detail = [
      clip.catcher_name || '',
      clip.steal_result || clip.pitch_type || '',
      clip.result && clip.result !== clip.steal_result ? clip.result : '',
      clip.game_date || '',
      clip.league || '',
      clip.angle || '',
    ].filter(Boolean).join(' · ');
    return `<div class="clip-card" data-url="${esc(clip.url)}" data-title="${esc(title)}" data-detail="${esc(detail)}">
      <h3>${esc(title)}</h3>
      <p>${esc(detail || 'Synergy clip')}</p>
      <span class="tag">${/\.mp4/i.test(clip.url) ? 'Play clip' : 'Play stream'}</span>
    </div>`;
  }

  function play(url, title, detail) {
    const video = $('video');
    $('dock').hidden = false;
    $('dock-title').textContent = title || 'Clip';
    $('dock-detail').textContent = detail || '';
    if (state.hls) { try { state.hls.destroy(); } catch (e) {} state.hls = null; }
    video.pause();
    video.removeAttribute('src');
    video.load();
    const isHls = /\.m3u8($|\?)/i.test(url);
    if (isHls && window.Hls && Hls.isSupported()) {
      state.hls = new Hls({ enableWorker: true });
      state.hls.loadSource(url);
      state.hls.attachMedia(video);
      state.hls.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => {}); });
    } else if (isHls && video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
      video.play().catch(() => {});
    } else {
      // Progressive Synergy short clips (.../clip/.../clip.mp4) play natively.
      video.src = url;
      video.play().catch(() => {});
    }
  }

  function closeDock() {
    const video = $('video');
    video.pause();
    if (state.hls) { try { state.hls.destroy(); } catch (e) {} state.hls = null; }
    video.removeAttribute('src');
    $('dock').hidden = true;
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  boot();
})();
