(() => {
  const state = {
    catalog: null,
    team: null,
    kind: null, // pitcher | catcher
    playerId: null,
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
    $('meta').textContent = [
      `Updated ${c.updated || '—'}`,
      `${(c.teams || []).length} teams`,
      `stream-only (no local mp4)`,
    ].join(' · ');
    renderTeams();
    const first = (c.teams || [])[0];
    if (first) selectTeam(first.abbrev);
    $('dock-close').onclick = closeDock;
  }

  function renderTeams() {
    const root = $('team-tabs');
    root.innerHTML = (state.catalog.teams || []).map((t) =>
      `<button type="button" data-team="${t.abbrev}" class="${t.abbrev === state.team ? 'active' : ''}">${t.abbrev}<br><span class="muted">${t.name}</span></button>`
    ).join('');
    root.querySelectorAll('button').forEach((btn) => {
      btn.onclick = () => selectTeam(btn.dataset.team);
    });
  }

  function teamPack(abbrev) {
    return (state.catalog.teams || []).find((t) => t.abbrev === abbrev) || null;
  }

  function selectTeam(abbrev) {
    state.team = abbrev;
    state.kind = null;
    state.playerId = null;
    renderTeams();
    renderLists();
    $('empty').hidden = false;
    $('player-panel').hidden = true;
    closeDock();
  }

  function renderLists() {
    const pack = teamPack(state.team);
    const pitchers = ((pack && pack.pitchers) || []).slice().sort((a, b) =>
      String(a.last || a.name).localeCompare(String(b.last || b.name))
    );
    const catchers = ((pack && pack.catchers) || []).slice().sort((a, b) =>
      String(a.last || a.name).localeCompare(String(b.last || b.name))
    );
    $('pitcher-list').innerHTML = pitchers.map((p) => {
      const n = (p.clips || []).filter((c) => c && c.url).length;
      return `<button type="button" data-kind="pitcher" data-id="${p.id}" class="${state.kind==='pitcher'&&String(state.playerId)===String(p.id)?'active':''}">
        ${esc(p.name)}<span class="badge">${n}/2</span></button>`;
    }).join('') || `<div class="muted">No pitchers</div>`;
    $('catcher-list').innerHTML = catchers.map((p) => {
      const n = (p.clips || []).filter((c) => c && c.url).length;
      return `<button type="button" data-kind="catcher" data-id="${p.id}" class="${state.kind==='catcher'&&String(state.playerId)===String(p.id)?'active':''}">
        ${esc(p.name)}<span class="badge">${n}</span></button>`;
    }).join('') || `<div class="muted">No catchers</div>`;
    document.querySelectorAll('#pitcher-list button, #catcher-list button').forEach((btn) => {
      btn.onclick = () => selectPlayer(btn.dataset.kind, btn.dataset.id);
    });
  }

  function findPlayer(kind, id) {
    const pack = teamPack(state.team);
    if (!pack) return null;
    const list = kind === 'catcher' ? pack.catchers : pack.pitchers;
    return (list || []).find((p) => String(p.id) === String(id)) || null;
  }

  function selectPlayer(kind, id) {
    state.kind = kind;
    state.playerId = id;
    renderLists();
    const p = findPlayer(kind, id);
    if (!p) return;
    $('empty').hidden = true;
    $('player-panel').hidden = false;
    $('player-name').textContent = p.name;
    $('player-sub').textContent = kind === 'pitcher'
      ? `${state.team} · runner on 1B · one fastball + one offspeed`
      : `${state.team} · throw down to 2B (SB / CS)`;
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
        $('clip-grid').innerHTML = clipCard('Throw to 2B', null);
      } else {
        $('clip-grid').innerHTML = clips.map((c, i) =>
          clipCard(c.label || `Throw ${i + 1}`, c)
        ).join('');
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
      clip.pitch_type || '',
      clip.result || '',
      clip.game_date || '',
      clip.league || '',
      clip.angle || '',
    ].filter(Boolean).join(' · ');
    return `<div class="clip-card" data-url="${esc(clip.url)}" data-title="${esc(title)}" data-detail="${esc(detail)}">
      <h3>${esc(title)}</h3>
      <p>${esc(detail || 'Synergy HLS')}</p>
      <span class="tag">Play stream</span>
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
    if (window.Hls && Hls.isSupported() && /\.m3u8($|\?)/i.test(url)) {
      state.hls = new Hls({ enableWorker: true });
      state.hls.loadSource(url);
      state.hls.attachMedia(video);
      state.hls.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => {}); });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
      video.play().catch(() => {});
    } else {
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
