// Travel branding editor (plan item 17 / SPEC 7.6) - the #admin-btn gear
// is otherwise dead weight on a shareable export (openAdminPanel() only
// does server-only calls that don't exist here), so it gets repurposed to
// this instead - see the TRAVEL_MODE init block below for the retargeting.
window.openTravelBranding = function() {
  const b = loadTravelBranding();
  const zoomKey = 'travelUiZoom', zoomDef = 1.18, zoomMin = 0.8, zoomMax = 2.0;
  let zoom = parseFloat(localStorage.getItem(zoomKey));
  if (!(zoom >= zoomMin && zoom <= zoomMax)) zoom = zoomDef;
  document.getElementById('modal-box').className = 'modal-box';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      <div class="title">🎨 Branding &amp; Display</div>
    </div>
    <div class="entries" style="display:block; max-height:75vh; overflow-y:auto; padding:16px;">
      <p class="admin-note">Changes here only affect this copy - they're saved on this device and travel back
      home in travel-changes.json, they never touch the original library.</p>
      <div class="admin-field">
        <label class="admin-label">Title</label>
        <input class="admin-input" type="text" id="branding-title" placeholder="${escapeHtml(APP_NAME)}" value="${escapeHtml(b.title || '')}">
      </div>
      <div class="admin-field">
        <label class="admin-label">Subtitle</label>
        <input class="admin-input" type="text" id="branding-subtitle" placeholder="${escapeHtml(APP_TAGLINE)}" value="${escapeHtml(b.subtitle || '')}">
      </div>
      <div class="admin-field">
        <label class="admin-label">Header image</label>
        <input class="admin-input" type="file" accept="image/*" id="branding-hero-file">
        ${b.heroDataUrl ? '<p class="admin-note">A custom header image is set. Choosing a new file replaces it; Reset below clears it.</p>' : ''}
      </div>
      <div class="admin-field">
        <label class="admin-label">Interface size: <span id="branding-zoom-value">${Math.round(zoom * 100)}%</span></label>
        <input type="range" id="branding-zoom-slider" min="${zoomMin}" max="${zoomMax}" step="0.05" value="${zoom}" style="width:100%;">
      </div>
      <div class="admin-row admin-field" style="margin-top:12px;">
        <button class="header-btn" type="button" onclick="onTravelBrandingSave()">Save</button>
        <button class="header-btn" type="button" onclick="onTravelBrandingReset()">Reset to defaults</button>
      </div>
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('branding-zoom-slider').addEventListener('input', (ev) => {
    const z = parseFloat(ev.target.value);
    document.getElementById('branding-zoom-value').textContent = Math.round(z * 100) + '%';
    document.documentElement.style.zoom = z;
    localStorage.setItem(zoomKey, String(z));
  });
};

window.onTravelBrandingSave = function() {
  const finish = (heroDataUrl) => {
    const b = loadTravelBranding();
    const title = document.getElementById('branding-title').value.trim();
    const subtitle = document.getElementById('branding-subtitle').value.trim();
    const next = {
      title: title || null,
      subtitle: subtitle || null,
      heroDataUrl: heroDataUrl !== undefined ? heroDataUrl : (b.heroDataUrl || null),
    };
    saveTravelBranding(next);
    applyTravelBranding();
    closeModal();
    if (typeof showToast === 'function') showToast('Branding saved.');
  };
  const fileInput = document.getElementById('branding-hero-file');
  const file = fileInput && fileInput.files && fileInput.files[0];
  if (!file) { finish(undefined); return; }
  const reader = new FileReader();
  reader.onload = () => finish(reader.result);
  reader.onerror = () => finish(undefined);
  reader.readAsDataURL(file);
};

window.onTravelBrandingReset = function() {
  localStorage.removeItem(TRAVEL_BRANDING_KEY);
  localStorage.removeItem('travelUiZoom');
  applyTravelBranding();
  document.documentElement.style.zoom = 1.18;
  closeModal();
  if (typeof showToast === 'function') showToast('Branding reset to defaults.');
};

// Admin panel - corrections (wrong TMDB match, custom poster) and adding a
// new physical disc, all without editing a file or running a script by
// hand. See app/web_server.py's /api/tmdb-search, /api/tmdb-posters,
// /api/override, /api/physical/add.
const adminState = { fixShowIdx: null, addPick: null, resolvedDeepLink: null };

window.openAdminPanel = function() {
  adminState.fixShowIdx = null;
  adminState.addPick = null;
  adminState.resolvedDeepLink = null;
  document.getElementById('modal-box').className = 'modal-box modal-box-roomy';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      <div class="title">⚙️ Admin</div>
    </div>
    <div class="entries" style="display:block; max-height:75vh; overflow-y:auto; padding:16px;">
      <div class="admin-tabs">
        <button class="admin-tab active" data-tab="fix" onclick="switchAdminTab('fix')" type="button">Fix a title</button>
        <button class="admin-tab" data-tab="add" onclick="switchAdminTab('add')" type="button">Add a title</button>
        <button class="admin-tab" data-tab="log" onclick="switchAdminTab('log')" type="button">Refresh &amp; Server Log</button>
      </div>
      <div id="admin-tab-fix" class="admin-tab-panel">
        <div class="admin-field" style="max-width:280px; margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid var(--border);">
          <label class="admin-label">Missing-episode grace period</label>
          <select class="admin-select" id="admin-missing-grace-days" onchange="onAdminMissingGraceDaysChange(this.value)" ${SERVER_MODE ? '' : 'disabled'}>
            <option value="7">7 days</option>
            <option value="14">14 days</option>
            <option value="21">21 days</option>
            <option value="28">28 days</option>
          </select>
          <p class="admin-note">An episode only counts as missing once it's aired more than this many days ago - avoids flagging something still working its way through downloads.</p>
        </div>
        <div class="admin-field" style="max-width:340px; margin-bottom:16px; padding-bottom:14px; border-bottom:1px solid var(--border);">
          <label class="admin-label">Cached posters &amp; images</label>
          <p class="admin-note" id="admin-image-cache-status">Checking...</p>
          <button class="header-btn" type="button" onclick="onAdminClearImageCache()" ${SERVER_MODE ? '' : 'disabled'}>Clear image cache</button>
          <p class="admin-note">Every TMDB poster/backdrop/cast photo is cached locally forever once shown - nothing is ever deleted automatically. Clearing here just re-downloads them fresh next time they're viewed. Your own uploaded covers and the manual disc covers are never touched by this.</p>
        </div>
        <div class="admin-field">
          <label class="admin-label">Search your library</label>
          <input class="admin-input" type="text" id="admin-fix-search" placeholder="Type a title..." oninput="onAdminFixLibrarySearch(this.value)" ${SERVER_MODE ? '' : 'disabled'}>
        </div>
        <div id="admin-fix-library-results" class="admin-pick-grid"></div>
        <div id="admin-fix-selected"></div>
      </div>
      <div id="admin-tab-add" class="admin-tab-panel" style="display:none">
        <div class="admin-field" style="max-width:220px;">
          <label class="admin-label">Source</label>
          <select class="admin-select" id="admin-add-source" onchange="onAdminAddSourceChange()">
            <option value="physical">📀 Physical disc</option>
            <option value="amazon">🛒 Amazon purchase</option>
            <option value="netflix">📺 Streaming - Netflix</option>
            <option value="prime">📺 Streaming - Prime Video</option>
          </select>
        </div>
        <div class="admin-field" id="admin-add-streaming-link" style="display:none; max-width:520px;">
          <label class="admin-label">Have the real Netflix/Prime page? Paste it here</label>
          <div class="admin-row">
            <input class="admin-input" type="text" id="admin-add-streaming-url" placeholder="https://www.netflix.com/title/... or amazon.com/gp/video/detail/..." ${SERVER_MODE ? '' : 'disabled'}>
            <button class="header-btn" type="button" onclick="onAdminResolveStreamingLink()" ${SERVER_MODE ? '' : 'disabled'}>Resolve title</button>
          </div>
          <div class="admin-note">Reads the real title straight off that page and fills in the search below with
          it - a typed guess is what usually goes wrong for a brand-new or oddly-titled release. Optional; you can
          still just type a title yourself instead.</div>
        </div>
        <div class="admin-row admin-field">
          <div class="admin-field">
            <label class="admin-label">Title</label>
            <input class="admin-input" type="text" id="admin-add-query" placeholder="Search TMDB..." ${SERVER_MODE ? '' : 'disabled'}>
          </div>
          <div class="admin-field" style="flex:0 0 110px;">
            <label class="admin-label">Type</label>
            <select class="admin-select" id="admin-add-type">
              <option value="Movie">Movie</option>
              <option value="TV">TV</option>
            </select>
          </div>
          <button class="header-btn" type="button" onclick="onAdminAddSearch()" ${SERVER_MODE ? '' : 'disabled'}>Search</button>
        </div>
        <div id="admin-add-results" class="admin-pick-grid"></div>
        <div id="admin-add-selected"></div>
      </div>
      <div id="admin-tab-log" class="admin-tab-panel" style="display:none">
        <div id="admin-log-content"><p class="admin-note">Loading...</p></div>
      </div>
      ${SERVER_MODE ? '' : '<p class="admin-note">Start the local server to use the Admin panel.</p>'}
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('open');
  loadAdminMissingGraceDays();
  loadAdminImageCacheStatus();
};

// The Field Guide button (next to Help) - only present when the pack provides a
// field_guide.html (served at /guide) or sets field_guide_url. No 'noopener' in
// window.open() here - with that flag Chrome returns null even on success,
// which used to trip the "popup blocked" fallback and pop the in-app Help
// modal at the same time.
const FIELD_GUIDE_URL = @@field_guide_url_json@@;