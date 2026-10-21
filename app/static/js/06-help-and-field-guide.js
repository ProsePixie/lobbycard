window.openFieldGuide = function() {
  if (SERVER_MODE || TRAVEL_SERVER_MODE) { window.open('/guide', '_blank'); return; }
  if (navigator.onLine === false) { openHelpGuide(); return; }  // static snapshot, offline
  window.open(FIELD_GUIDE_URL, '_blank');
};

// Help (in-app fallback) - plain static content, no dynamic data needed, so this is
// just one big template literal rather than anything built up piece by
// piece. Collapsible <details> sections (not a wall of text) so it's
// actually scannable - added 2026-08-22 once the feature set genuinely
// settled down, per the user's own long-standing request ("write it once
// things are perfect... too many cool things to keep track of").
window.openHelpGuide = function() {
  document.getElementById('modal-box').className = 'modal-box modal-box-roomy';
  document.getElementById('modal-box').innerHTML = `
    <div class="modal-header">
      <button class="modal-close" onclick="closeModal()">&times;</button>
      <div class="title">Help</div>
    </div>
    <div class="entries" style="display:block; max-height:75vh; overflow-y:auto; padding:16px;">
      <div class="help-guide">
        <p class="help-intro">A plain-English tour of everything @@app_name@@ does. Nothing here needs to be memorized -
        this button is always here, so just come back whenever you forget what some icon does. Click any section
        to open it.</p>

        <div class="help-callout">
          🎬 <strong>New to this?</strong> @@app_name@@ is a browsable catalog of everything in the video collection -
          TV shows, movies, whatever's on physical disc, whatever was bought digitally - all in one searchable
          place, with real posters, cast info, air dates, and a watched/unwatched history it keeps for you. It
          reads the actual files every night on its own and updates itself - day to day, nobody has to run
          anything or tell it about something new; it already knows by the next morning. Everything below is
          just how to get around it.
        </div>

        <details class="help-section" open>
          <summary>The featured buttons up front</summary>
          <div class="help-body">
            <p>These sit first in the header and are noticeably bigger than everything to their right, on
            purpose - they change <em>what you're looking at</em>, not just do a one-off action, so they're
            built to stand out at a glance.</p>
            <ul style="list-style:none; padding-left:0;">
              <li style="margin-bottom:10px;"><span class="icon-chip" style="width:26px; height:26px; border-radius:8px; background:var(--btn-watchlist-soft); display:inline-flex; align-items:center; justify-content:center; vertical-align:-8px; margin-right:6px;"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="var(--btn-watchlist)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4"></path></svg></span><strong>Watch List</strong> - everything either of you has starred (see "⭐ Watch List" under Opening a title, below, for how something gets added).</li>
              <li style="margin-bottom:10px;"><span class="icon-chip" style="width:26px; height:26px; border-radius:8px; background:var(--btn-weekly-soft); display:inline-flex; align-items:center; justify-content:center; vertical-align:-8px; margin-right:6px;"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="var(--btn-weekly)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"></rect><path d="M8 3v4M16 3v4M3 10h18"></path><path d="M16.5 14a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z"></path><path d="M16.5 15.3v1.4l1 .6"></path></svg></span><strong>Weekly TV</strong> - narrows the whole library down to just shows that are currently airing <em>and</em> have a real, TMDB-confirmed next-episode date, soonest-airing first, with the day of the week shown on each one. A show drops off this list the moment it goes on hiatus with no announced date yet, or wraps for the season - same colored dot as the ⚡"Airs Soon" indicator elsewhere in the app. Click it again to go back to normal browsing.</li>
              <li style="margin-bottom:10px;"><span class="icon-chip" style="width:26px; height:26px; border-radius:8px; background:var(--accent-soft); display:inline-flex; align-items:center; justify-content:center; vertical-align:-8px; margin-right:6px; padding:2px;"><img src="@@franchise_icon_uri@@" alt="" style="width:100%;height:100%;object-fit:contain;"></span><strong>@@franchise_name@@ mode</strong> - an optional ordered set of titles (a saga or universe) with its own look. Big enough a feature it gets its own section right below.</li>
              <li style="margin-bottom:10px;"><span class="icon-chip" style="width:26px; height:26px; border-radius:8px; background:var(--btn-purge-soft); display:inline-flex; align-items:center; justify-content:center; vertical-align:-8px; margin-right:6px;"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="var(--btn-purge)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.582 6.186a2.18 2.18 0 0 0-1.537-1.552C18.254 4.2 12 4.2 12 4.2s-6.255 0-7.945.434a2.18 2.18 0 0 0-1.537 1.552A22.9 22.9 0 0 0 2.1 10.4a22.9 22.9 0 0 0 .418 4.214 2.18 2.18 0 0 0 1.537 1.552c1.69.434 7.945.434 7.945.434s6.254 0 7.945-.434a2.18 2.18 0 0 0 1.537-1.552 22.9 22.9 0 0 0 .418-4.214 22.9 22.9 0 0 0-.418-4.214z"></path><path d="M9.75 13.3 15.3 10.4 9.75 7.5z" fill="currentColor" stroke="none"></path></svg></span><strong>YouTube</strong> - swaps the whole library view for a curated YouTube channel feed instead. Only shows up when that block is actually set up. Its own section is right below Franchise Mode's.</li>
            </ul>
            <div class="help-callout">
              📌 <strong>@@franchise_name@@ mode and YouTube are an either/or pair</strong> - turning one on automatically
              turns the other off. Having both on at once wouldn't mean anything coherent (one filters the
              library to a title set, the other replaces the library view entirely), so they're not allowed
              to both be active.
            </div>
          </div>
        </details>

        <details class="help-section">
          <summary><span class="icon-chip" style="width:20px; height:20px; border-radius:5px; background:var(--accent-soft); display:inline-flex; vertical-align:-5px; margin-right:4px; padding:2px;"><img src="@@franchise_icon_uri@@" alt="" style="width:100%;height:100%;object-fit:contain;"></span>@@franchise_name@@ mode</summary>
          <div class="help-body">
            <p>An optional mode for a saga, universe or any hand-picked set of titles in a defined order - it
            only appears once the active pack defines one (see <code>personal.example/franchise.json</code>).
            Toggling it on (the header button, or the matching filter-row button - they stay in sync)
            narrows everything down to that set and replaces the usual three panels with a single "What's Next"
            recommendation. Every title in the set also gets its own timeline badge in its popup, showing where
            it sits - with a button right there to flip between story order and release order, and that
            choice applies everywhere at once. The banner up top reskins into the pack's own palette while it
            is on, too. Turning this on automatically turns YouTube Mode off, if it was on.</p>
          </div>
        </details>

        <details class="help-section">
          <summary><span class="icon-chip" style="width:20px; height:20px; border-radius:5px; background:var(--btn-purge-soft); display:inline-flex; vertical-align:-5px; margin-right:4px;"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="var(--btn-purge)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.582 6.186a2.18 2.18 0 0 0-1.537-1.552C18.254 4.2 12 4.2 12 4.2s-6.255 0-7.945.434a2.18 2.18 0 0 0-1.537 1.552A22.9 22.9 0 0 0 2.1 10.4a22.9 22.9 0 0 0 .418 4.214 2.18 2.18 0 0 0 1.537 1.552c1.69.434 7.945.434 7.945.434s6.254 0 7.945-.434a2.18 2.18 0 0 0 1.537-1.552 22.9 22.9 0 0 0 .418-4.214 22.9 22.9 0 0 0-.418-4.214z"></path><path d="M9.75 13.3 15.3 10.4 9.75 7.5z" fill="currentColor" stroke="none"></path></svg></span>YouTube</summary>
          <div class="help-body">
            <p>An independently-owned block that renders inside @@app_name@@ rather than a real library feature - it's
            its own small app, just wired in here so there's one place to look. Turning it on replaces the whole
            library view (search, filters, the three panels, the grid) with a curated feed of channels/videos
            instead; turning it off goes straight back to normal browsing exactly where you left it. No account,
            no sign-in, no API key - it reads public RSS/watch-page data only. Playback opens your real default
            browser, not a window inside @@app_name@@. "Mark watched" here is a manual click, same idea as everywhere
            else in the app - nothing here is auto-tracked. Turning this on automatically turns @@franchise_name@@ mode off,
            if it was on.</p>
          </div>
        </details>

        <details class="help-section">
          <summary>The main screen</summary>
          <div class="help-body">
            <h4>Search &amp; filters</h4>
            <p>The search box, the All/TV/Movies chips, and the genre/date/sort dropdowns all combine - each one
            narrows down whatever the others already picked, they don't replace each other.</p>
            <div class="help-callout">
              📌 <strong>The Source dropdown defaults to "Digital only"</strong> every time the page loads.
              If something you own physically or bought on Amazon seems to be missing, this is almost always
              why - switch it to "Digital + Physical" (or "Physical only") to see everything. This one default
              has caused more "wait, where did my show go?" moments than anything else in the app.
            </div>
            <h4>The colored dots</h4>
            <p>Every card shows one or more small colored dots - click any dot in the legend row to filter by it,
            click again to clear:</p>
            <ul>
              <li><strong style="color:#d97706;">🟠 Unwatched</strong> - still has something you haven't seen</li>
              <li><strong style="color:#9333ea;">🟣 In Progress</strong> - partway through</li>
              <li><strong style="color:#2563eb;">🔵 Active</strong> - TMDB says it's still airing/in production</li>
              <li><strong style="color:#64748b;">⚪ Ended</strong> - no longer airing</li>
              <li><strong style="color:#16a34a;">🟢 Watched</strong> - fully caught up</li>
              <li><strong style="color:#0891b2;">🔷 Airs Soon</strong> - TMDB has a real next-episode air date for it</li>
            </ul>
            <h4>The small icon buttons next to the sort dropdown</h4>
            <p>Left to right: the <strong>franchise icon</strong> (when the pack defines one) narrows the whole
            library down to just that title set (same as the header's franchise button - the two stay in
            sync). The <strong>red circle-slash ("Error")</strong> shows movies whose file runtime doesn't
            match what TMDB reports for that title - usually a wrong match, but sometimes just a director's
            cut or alternate edit (see the Admin panel section below for how to tell the app "no, this one's
            fine"). The <strong>clock/stopwatch icon</strong> narrows to TV shows whose episodes are short
            (under 30 minutes each, per TMDB) - handy for finding something quick to watch. The two
            <strong>🙈</strong> buttons with @@partner_initial@@ or @@you_initial@@ narrow the grid down to <em>only</em> what that person
            has marked "not interested in" - an audit view for seeing what they've ruled out, not a way to
            hide it from browsing. Click again to go back to normal. See the popup section below for how a
            title gets marked not interested in the first place.</p>
            <h4>The "x / y" number on a TV show's card</h4>
            <p>Watched episodes over total episodes you actually own - e.g. "20/49 eps" means you've checked
            off 20 of the 49 episodes of that show sitting in your library. It's <strong>not</strong> how many
            episodes exist worldwide (see Missing Episodes in the Admin section below for that) - just your own
            watched-vs-owned progress on what's already here.</p>
          </div>
        </details>

        <details class="help-section">
          <summary>The three panels up top</summary>
          <div class="help-body">
            <p>These three look similar at a glance but each one is tracking something genuinely different -
            here's exactly what each one means:</p>
            <h4>What's New</h4>
            <p>Anything <strong>added to your library</strong> in the last 7 days - based on when the file
            itself was scanned in, not when it was released and not whether you've watched it. A brand-new
            season of a show you've watched for years shows up here right next to something you've never
            touched, because "new" means "new to your collection," not "new content picked for you."</p>
            <h4>Continue Watching</h4>
            <p><strong>TV shows only</strong> (never movies), recalculated fresh every time you load the
            page. A show belongs here whenever <strong>both</strong> of these are true: it still has at
            least one unwatched episode actually sitting in your library right now, <em>and</em> you've
            watched something from it recently enough that it shows up in your own Recently Watched list
            (see below). It drops off the moment you finish it (nothing unwatched left) or once it ages out
            of Recently Watched without you having watched anything else from it.</p>
            <p>That second condition is what makes this work correctly for a "Delete After Watching" show,
            where Purge moves each episode out of your library the moment you watch it - so the episode(s)
            you've actually seen are usually already gone, not sitting there marked watched. Without
            checking Recently Watched too, a show like that would look exactly like "never started" the
            moment Purge ran, even mid-series with real unwatched episodes still waiting. Checking Recently
            Watched is what tells "genuinely never started this" apart from "started it, and the watched
            part is just gone now" - the same rule applies to every show, "Keep Forever" included, not just
            Delete After Watching ones.</p>
            <h4>Recently Watched</h4>
            <p>This one works differently from the other two: it isn't recalculated from your library each
            time - it's a running history that remembers, in order, whatever you last checked the watched
            box on. Check off a single episode, a whole season at once, or a movie, and that title jumps to
            the top here, even if you'd already watched other episodes of it a while ago. It's less "what
            have I been watching" and more "what did I last mark watched" - and, as of the fix above, it's
            also the thing Continue Watching itself leans on to keep working after Purge clears out the
            episodes you've already seen.</p>
            <div class="help-callout">
              📌 <strong>Quick way to tell them apart:</strong> What's New = new arrivals (doesn't care if
              watched). Continue Watching = still has something unwatched <em>and</em> you've watched
              something from it recently. Recently Watched = your own click history (remembers the order
              you checked things off, keeps the last 12) - and it's what Continue Watching itself checks to
              stay accurate for "Delete After Watching" shows. <strong>Clear</strong> on Recently Watched
              only empties that history list - it never un-checks anything you've actually marked watched,
              but it CAN make an in-progress "Delete After Watching" show drop out of Continue Watching too,
              since that's exactly the signal it depends on.
            </div>
            <p>Each panel has its own small <strong>↻</strong> button - it re-checks TMDB for updated air dates
            for just the handful of shows in that panel, so you don't have to wait for (or trigger) a full
            library refresh just to see if something new got announced.</p>
            <p>If you own enough of a franchise defined in the pack, this whole row swaps for a single
            <strong>What's Next</strong> panel instead once @@franchise_name@@ mode is on - it tells you the next unwatched
            title in story order (or release order, toggle available right there).</p>
          </div>
        </details>

        <details class="help-section">
          <summary>Opening a title</summary>
          <div class="help-body">
            <p>Click any poster to open its full detail popup. What's in there depends on the title, but the
            common pieces:</p>
            <h4>Watched checkboxes</h4>
            <p>Check any episode/movie off as watched, any time - nothing else in the app will ever flip this
            for you automatically. Seasons collapse into groups for shows with a lot of episodes, and a season's
            own checkbox at the top marks the whole thing at once.</p>
            <h4>📌 Retention</h4>
            <p>"Keep forever" (the default) vs "Delete after watching" - the second one is what the 🗑️ Purge
            button (see below) actually acts on, and only for things you've actually watched. A checkbox on any
            individual episode/movie ("keep this one") carves out an exception to the show's own setting - handy
            for a show you're mostly done with except one episode you still want around. This same dropdown and
            per-episode checkbox also work on the road in Travel Mode now (see that section below) - no server
            needed there either.</p>
            <h4>⭐ Watch List / 🙈 Not Interested</h4>
            <p>Two independent per-person buttons (one each for @@you_name@@ and @@partner_name@@) - starring something adds it to that
            person's Watch List (the header's suitcase-shaped icon... actually the stacked-books icon at the very
            left of the header). Marking 🙈 adds it to that person's "not interested" list - their filter
            button (up in the main filter row) narrows the grid down to <em>only</em> those, so you can see
            what they've ruled out - completely independent of the star, so one person disliking something
            doesn't affect the other's list at all.</p>
            <h4>🔄 Restart show</h4>
            <p>Marks every episode unwatched again, to track a rewatch from scratch. No confirmation dialog - it
            can't break anything, and you won't click it by accident.</p>
            <h4>🧳 Travel checkbox</h4>
            <p>Only shows up when Travel selection mode is on (see the Travel Mode section) - checks an
            episode/movie for inclusion on a portable export.</p>
            <h4>The banner image (TV shows)</h4>
            <p>For a TV show with more than one backdrop available on TMDB, small ‹ › arrows appear on the
            popup's own banner image, plus a page count and a "☆ use this" button - browse through TMDB's other
            options for that show and click it to pin your favorite as the one shown from now on (it becomes
            "★ saved"; click it again to un-pin back to the default). Works the same way in Travel Mode,
            offline.</p>
            <h4>Cast &amp; crew</h4>
            <p>Click any cast member's photo for a quick bio popup - birthday, where they're from, a short
            biography, and every other title already in the library that has them in the main cast, one click
            away to jump straight there. The photo and name appear immediately; the biography text loads a
            moment later the first time anyone looks that person up, then it's remembered and instant after
            that.</p>
            <h4>"More like this"</h4>
            <p>TMDB's own recommendations for that title. Hover a poster for a quick synopsis and rating without
            leaving the page. Click one and it does one of two things: if it's already something in the library
            (look for the small <strong>✓ In your library</strong> label), it opens that title right here;
            otherwise it opens that title's TMDB page in a new tab instead.</p>
            <h4>TMDB / IMDB / Trailer / epguides buttons</h4>
            <p>Whichever of these actually apply to a title show up as small buttons near the top of its popup -
            each one goes straight to that title's real page on that site, in a new tab.</p>
            <h4>The small colored dots next to each episode</h4>
            <p>Different from the big status dots on the main screen - these tiny ones next to an individual
            episode/movie entry (once expanded) show which physical location that specific copy is sitting in -
            still in the download queue, or already filed into the real archive.</p>
            <h4>Episode list controls</h4>
            <p>For shows with more than one episode, two small controls appear above the episode list itself:
            a sort toggle (oldest-first vs. newest-first) and, only when that show actually has any, a
            "Show extras" checkbox for bonus/behind-the-scenes content that got merged in alongside the real
            episodes - hidden by default so it doesn't clutter a normal episode list.</p>
          </div>
        </details>

        <details class="help-section">
          <summary><span class="icon-chip" style="width:20px; height:20px; border-radius:5px; background:var(--btn-travel-soft); display:inline-flex; vertical-align:-5px; margin-right:4px;"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="var(--btn-travel)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="8" width="18" height="12" rx="2"></rect><path d="M8 8V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"></path></svg></span>Travel Mode (taking your library on the road)</summary>
          <div class="help-body">
            <p>This makes a real, portable, fully offline copy of a chunk of your library on a separate drive -
            no server, no internet, no computer left running at home required.</p>
            <h4>Before you go</h4>
            <ol style="margin:0 0 10px; padding-left:20px;">
              <li>Check the 🧳 box next to whatever you want along (it's always there next to every episode/movie)
              - or "Select whole show" from inside a title's popup.</li>
              <li>Open the 🧳 <strong>Travel Mode</strong> button in the header, click <strong>Browse...</strong>
              to pick a real destination folder (a drive letter, a folder - anywhere with room) through a normal
              Windows folder picker. Choose <strong>Personal</strong> (your own branding, baked in) or
              <strong>Shareable</strong> (generic branding, no star-ratings/not-interested flags baked in - meant
              for handing the drive to someone else), then click <strong>Generate Export Script</strong>. This
              only writes a review-it-yourself script; nothing is copied yet.</li>
              <li>Run the generated script (it's also copied straight to your Desktop, as "Run Travel Export.vbs",
              for a real one-double-click action). A small progress window pops up on its own and shows live
              copy progress - safe to close once it says "Done." It copies the actual files plus a small
              self-contained "Travel Library.html" - open that file on any computer, no install needed, and it
              behaves like a lite version of this app: browsing, filtering, watched-checkboxes, the 📌 Retention
              dropdown/per-episode "keep this one" checkbox, and the banner-image picker all work fully offline.
              Your travel selection clears itself automatically once the copy finishes, so nothing carries over
              by accident into next time.</li>
            </ol>
            <h4>The first time you use the drive on a given computer</h4>
            <p>Open <strong>START HERE.html</strong> right on the drive - it's the short version of this whole
            section, written for whoever's actually sitting at that computer. The one thing worth doing:
            double-click <strong>Setup.cmd</strong> once. It only touches your own Windows account (no admin
            rights, nothing system-wide), takes a few seconds, and unlocks two things that don't work until it's
            run: Open Folder/Play links, and a real <strong>"Open Travel Library.lnk"</strong> shortcut on the
            drive itself. Browsing, watched marks, and retention already work the moment you open
            <code>Travel Library.html</code> directly - Setup.cmd is a convenience, not a requirement.
            <span style="display:block; margin-top:6px;">If this happens to be a computer that already has the
            real app installed (or another travel drive already set up), Setup.cmd notices and asks before
            replacing its Open Folder/Play links - it won't silently take over. Leaving a computer? Run
            <strong>Uninstall.cmd</strong> to remove what Setup.cmd added there - it never touches the drive's
            own media, images, or trip data, and never touches a different install if this drive happens to
            share the computer with one.</span></p>
            <h4>While you're there</h4>
            <p>Watching something to the end through the portable app's "▶ Play" link now marks it watched on
            its own, the same as it does at home - no need to check the box by hand afterward (checking it
            yourself still works too, of course). The travel drive also gets a fixed
            <strong>"New Downloads"</strong> folder - picked up something new on the trip? Drop the video
            file(s) in there, open the portable app's own 🧳 Travel Mode button, and click
            <strong>Scan New Downloads for Files</strong>: it does a live TMDB lookup (when you have a signal)
            and adds matched titles right into the browsing grid as "🆕 New Download," so you can watch them
            like anything else already on the drive. A <strong>Shareable</strong> copy also gets a
            🎨 <strong>Branding &amp; Display</strong> gear (where the ⚙️ Admin gear normally is - Admin itself
            needs a server, so it doesn't apply on the road) for setting a custom title/subtitle/header image
            and an interface-size slider, all just for that copy.</p>
            <h4>Optional: live sync while you're there</h4>
            <p>Double-click <strong>"Start Travel Server.cmd"</strong> on the drive (needs Python + Flask already
            on that computer - if they're not there, it just opens the plain offline copy instead, nothing
            breaks) to turn on a small local live-sync server for the rest of that session. With it running:
            watched marks and retention changes save live instead of only in the browser, a second device on the
            same network (a phone, another laptop) can watch along at the address it prints, and a matched
            "🆕 New Download" gets automatically filed into the drive's own <code>Media</code> folder with a
            proper name instead of just sitting in "New Downloads." None of this is required - close that window
            (or never open it) and everything above still works exactly the same, just without the live part.</p>
            <h4>When you're back</h4>
            <p>Click the portable app's own <strong>💾 Export Trip Changes</strong> button (top of its header) -
            this writes one file, <code>travel-changes.json</code>, straight to the drive's
            <code>travel-state</code> folder, right alongside <code>Travel Library.html</code>. It bundles
            everything that changed on the trip in one shot: watched marks, retention changes, new files found
            via Scan New Downloads, pinned banner images, and (on a Shareable copy) the branding you set - one
            export covers all of it, nothing separate to remember.</p>
            <p>Bring the drive home, plug it in, open the real app's 🧳 Travel Mode button again, and scroll down
            to <strong>Back from a trip?</strong>: Browse to the drive/export folder (the same one you exported
            to) and click <strong>Import Trip Changes</strong>. It reports exactly what it applied - watched
            marks, retention changes, new files, backdrops - re-verifies every new file against TMDB rather
            than trusting the export blindly, and never auto-deletes anything - a title you flagged "delete
            after watching" out
            there just gets reported here, your home copy is never touched automatically.</p>
          </div>
        </details>

        <details class="help-section">
          <summary><span class="icon-chip" style="width:20px; height:20px; border-radius:5px; background:var(--btn-purge-soft); display:inline-flex; vertical-align:-5px; margin-right:4px;"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="var(--btn-purge)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path></svg></span>File Management</summary>
          <div class="help-body">
            <p>One header button, three file-moving actions inside its popup:</p>
            <h4>Delete (Pending Deletion)</h4>
            <p>Moves anything watched-and-set-to-delete-after-watching to Pending Deletion for review. Never
            deletes outright - that's still your call, every time.</p>
            <h4>File to Library</h4>
            <p>Takes anything sitting in the download queue that's already watched and "keep forever," and files
            it properly into the real archive with the correct name.</p>
            <h4>File Download Queue</h4>
            <p>A picker for moving anything currently sitting in the download queue (TV or movies, watched or
            not) straight into the real archive on your own terms, not just automatically. Shows what's already
            selected by default and lets you deselect anything; a folder the companion tool already has its own eye on
            is flagged and left unchecked. Also sweeps genuinely empty/junk leftover folders (a stray sample
            file, nothing real) into Pending Deletion the same way Delete does. If a better-quality copy of
            something you already have shows up here, moving it automatically replaces the old one and sends
            the old one to Pending Deletion instead of leaving two copies around.</p>
          </div>
        </details>

        <details class="help-section">
          <summary><span class="icon-chip" style="width:20px; height:20px; border-radius:5px; background:var(--btn-admin-soft); display:inline-flex; vertical-align:-5px; margin-right:4px;"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="var(--btn-admin)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82A1.65 1.65 0 0 0 3 13.09H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg></span>Admin</summary>
          <div class="help-body">
            <p>One header button, five admin/behind-the-scenes tools inside its popup - meant to replace ever
            needing to hand-edit a file or ask for help:</p>
            <h4>📊 Library Dashboard</h4>
            <p>A popup of library-wide numbers: how many titles/episodes total, total size, how much space is
            duplicate waste (with a one-click export of exactly which files, once there's anything to export),
            what's currently sitting in the download queue vs. archived for good, and what's ready to be
            filed/purged/auto-filed right now. Nothing here needs regular attention - it's a "how are we doing"
            check, not a to-do list.</p>
            <h4>📋 Missing Episodes</h4>
            <p>Every episode TMDB says exists for a show that isn't in the files yet (a season that hasn't been
            grabbed, one skipped episode), grouped by show, with a one-click export to a plain-text checklist to
            take shopping/downloading from.</p>
            <h4>🗂 Library History</h4>
            <p>A read-only, permanent ledger of everything ever added to or removed from the library - a show
            arriving, episodes showing up, something getting removed or coming back. Useful for "wait, did I
            used to have this?" moments; nothing here is editable, it's just the record.</p>
            <h4>Fix / Add a Title</h4>
            <p>Two tabs:</p>
            <ul>
              <li><strong>Fix a title</strong> - search your library, then <strong>re-search TMDB</strong> (type a
              better query, pick the right result), <strong>🔎 try Epguides / IMDB / Wikipedia</strong> for the
              rare title TMDB's own search can't find at all (chains through epguides/TVmaze/IMDb and
              Wikipedia/Wikidata as a fallback - won't always find something, but worth a try before giving up),
              <strong>pick a different poster</strong> or <strong>upload your own image</strong> entirely (your
              own upload can also become the popup's banner image automatically, if it's wide/large enough),
              manually set a title's <strong>category/genre</strong> by hand when TMDB has none or the wrong one
              (a dropdown of every genre already in your library, plus free text for anything not in there yet),
              and if a title shows "⚠️ Unknown" because its file runtime doesn't match TMDB (the 🤷 Oops case) but
              the match is actually correct - just a director's cut, extended edition, or different broadcast
              length - there's a <strong>"✅ This is the right match"</strong> button right in that message.</li>
              <li><strong>Add a title</strong> - for something that isn't in your digital library yet: a
              <strong>physical disc</strong>, an <strong>Amazon purchase</strong>, or now also a title you're
              following on <strong>Netflix</strong> or <strong>Prime Video</strong> because it's currently
              streaming there (a live subscription counted the same as owning it - see the app behavior itself
              for how a followed streaming title shows up and can be released later). Search TMDB, confirm, fill
              in what you own, save - shows up within seconds, no script to run.</li>
            </ul>
            <p>Also inside <strong>Fix a title</strong>, two settings that apply to the whole library rather than
            one title:</p>
            <ul>
              <li><strong>Missing-episode grace period</strong> - a dropdown (7/14/21/28 days). An episode only
              counts as "missing" (see 📋 Missing Episodes above) once it's aired more than this many days ago -
              raise it if downloads sometimes take a while and you don't want to see something as missing while
              it's still working its way in.</li>
              <li><strong>Cached posters &amp; images</strong> - every poster/backdrop/cast photo is saved to
              disk the first time it's shown, so the app loads fast and works even if the internet's briefly
              down. Nothing is ever deleted from this on its own; <strong>Clear image cache</strong> here wipes
              it on purpose (everything just re-downloads next time it's viewed) - your own uploaded covers are
              never touched by this.</li>
            </ul>
            <p>And from inside a specific title's own popup, two more one-off overrides:</p>
            <ul>
              <li><strong>Mark this show complete</strong> - for a show TMDB still lists as "missing" episodes
              on (see 📋 Missing Episodes) that you actually consider done - stops it from showing up as missing
              until you turn the override off again.</li>
              <li><strong>Remove Forever</strong> - for a "Delete After Watching" show/movie you've decided
              you're done with entirely, not just done with the files you currently have. Moves everything of
              it to Pending Deletion (same safe review folder as Purge, never an instant delete) and stops
              tracking it - it won't be chased for new episodes or missing-episode checks anymore. Re-adding it
              later (see Add a title above) undoes this completely.</li>
            </ul>
            <h4>📄 Logs</h4>
            <p>A shortcut straight into Fix / Add a Title's own "Refresh &amp; Server Log" tab - a plain-English
            summary of the last library refresh (what ran, what changed, anything that failed), plus the raw
            server log underneath if you ever need the real detail. Same content either way; this is just the
            faster way in when logs are specifically what you're after.</p>
          </div>
        </details>

        <details class="help-section">
          <summary><span class="icon-chip" style="width:20px; height:20px; border-radius:5px; background:var(--btn-refresh-soft); display:inline-flex; vertical-align:-5px; margin-right:4px;"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="var(--btn-refresh)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5"></path></svg></span>Refresh library</summary>
          <div class="help-body">
            <p>Runs the full pipeline right now instead of waiting for the nightly run - rescans your files,
            re-checks TMDB, updates everything. Takes a minute or two; a "Loading" overlay blocks the page until
            it's done, so you always see fresh results, never a half-updated screen.</p>
            <div class="help-callout">
              📌 <strong>Not the same as "Refresh Page"</strong> - that separate button, top-right of the
              banner, is just a plain browser reload (like hitting F5). It doesn't touch your library at all,
              it's only useful if the page itself looks stuck or glitched. This button is the one that actually
              rescans anything.
            </div>
          </div>
        </details>

        <details class="help-section">
          <summary><span class="icon-chip" style="width:20px; height:20px; border-radius:5px; background:var(--accent-soft); display:inline-flex; vertical-align:-5px; margin-right:4px;"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M9.5 9a2.5 2.5 0 0 1 4.6 1.4c0 1.6-2.1 1.9-2.1 3.4"></path><path d="M12 17.5h.01"></path></svg></span>Help</summary>
          <div class="help-body">
            <p>The button you're using right now - it opens a small popup with two items:</p>
            <h4>Help Guide</h4>
            <p>This document - a written reference, meant for scanning: click a section, read what you need,
            close it. Nothing to walk through step by step.</p>
            <h4>Field Guide</h4>
            <p>A separate, visual, illustrated companion - the icon map and step-by-step walkthroughs for things
            that are easier to show than describe in a wall of text. Opens in a new tab; works offline too if
            you've got the local server running.</p>
          </div>
        </details>

        <details class="help-section">
          <summary>Keeping the server running</summary>
          <div class="help-body">
            <p>Start it with <code>python run.py</code> (or the VORTEX launcher, if you use it). If the server's
            already running, just open the page again. A launcher can also open a friendly "Starting up..." tab
            that waits on its own and jumps to the real app the instant it's ready - no need to refresh
            anything yourself.</p>
            <p>If you ever see an error box saying it never came up: that's real and means something's actually
            wrong (not just slow) - the message itself will tell you whether the server process is still running
            or has already stopped, which is the key clue for figuring out why.</p>
          </div>
        </details>

        <p class="help-intro" style="margin-top:16px;">Still stuck on something, or something feels genuinely
        broken rather than just confusing? Just ask - that's still always the fastest path.</p>
      </div>
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('open');
};
