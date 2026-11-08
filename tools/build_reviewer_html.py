import json
from pathlib import Path

registry_path = Path('data/headshots/donors/registry.json')
with open(registry_path, 'r', encoding='utf-8') as f:
    registry = json.load(f)

divisions = {
    'All (32)': list(registry.keys()),
    'AFC East': ['BUF', 'MIA', 'NE', 'NYJ'],
    'AFC North': ['BAL', 'CIN', 'CLE', 'PIT'],
    'AFC South': ['HOU', 'IND', 'JAX', 'TEN'],
    'AFC West': ['DEN', 'KC', 'LV', 'LAC'],
    'NFC East': ['DAL', 'NYG', 'PHI', 'WAS'],
    'NFC North': ['CHI', 'DET', 'GB', 'MIN'],
    'NFC South': ['ATL', 'CAR', 'NO', 'TB'],
    'NFC West': ['ARI', 'LAR', 'SF', 'SEA']
}

tier_meta = {
    'tier1_speed': {'label': 'Tier 1: Speed', 'range': '< 200 lbs', 'color': 'emerald', 'desc': 'CB, WR, K, P — Slender neck, narrow shoulders'},
    'tier2_skill': {'label': 'Tier 2: Skill', 'range': '200–224 lbs', 'color': 'blue', 'desc': 'QB, RB, WR, Safety — Athletic muscular neck'},
    'tier3_power': {'label': 'Tier 3: Power', 'range': '225–254 lbs', 'color': 'purple', 'desc': 'LB, TE, FB, Power QB — Thick muscular neck'},
    'tier4_edge': {'label': 'Tier 4: Edge', 'range': '255–289 lbs', 'color': 'amber', 'desc': 'DE, EDGE, In-line TE — Imposing V-taper, wide traps'},
    'tier5_lineman': {'label': 'Tier 5: Lineman', 'range': '>= 290 lbs', 'color': 'rose', 'desc': 'OT, OG, C, DT, NT — Massive 20+ in neck, wide jaw'}
}

registry_json_str = json.dumps(registry).replace("'", "\\'")

html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Project Mu — 32-Team Donor Template Reviewer</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    /* Custom scrollbar */
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 4px; }}
    .donor-img {{ transition: transform 0.2s ease; }}
    .donor-img:hover {{ transform: scale(1.04); }}
  </style>
</head>
<body class="bg-[var(--background)] text-[var(--foreground)] antialiased min-h-screen p-4 md:p-6">

  <!-- Header -->
  <header class="max-w-7xl mx-auto mb-6">
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-[var(--border)] pb-5">
      <div>
        <div class="flex items-center gap-2 mb-1">
          <span class="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
            5-Bracket Weight System
          </span>
          <span class="text-xs text-[var(--muted-foreground)]">32 Franchises • 160 Donors</span>
        </div>
        <h1 class="text-2xl font-bold tracking-tight">NFL Donor Template Inspection & Disqualification Studio</h1>
        <p class="text-sm text-[var(--muted-foreground)] mt-1">
          Review authentic media-day portraits across all 5 physical weight tiers. Disqualify any awkward necklines, deep V-necks, or dreadlock obstructions, and instantly swap to alternative roster candidates.
        </p>
      </div>

      <div class="flex items-center gap-3">
        <button onclick="exportDecisions()" class="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)] text-xs font-medium hover:opacity-90 transition shadow-sm">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"/></svg>
          Export Decisions (<span id="change-count">0</span>)
        </button>
      </div>
    </div>

    <!-- Navigation & Filter Bar -->
    <div class="mt-4 flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3">
      <!-- Division selector -->
      <div class="flex items-center gap-1.5 overflow-x-auto pb-1" id="div-filters">
        <!-- populated by JS -->
      </div>

      <!-- Search & Tier Filter -->
      <div class="flex items-center gap-2">
        <div class="relative">
          <input type="text" id="search-input" oninput="filterTeams()" placeholder="Search team or player..." 
            class="w-48 md:w-64 px-3 py-1.5 text-xs bg-[var(--card)] border border-[var(--border)] rounded-lg text-[var(--foreground)] placeholder-[var(--muted-foreground)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]">
        </div>
        <select id="tier-filter" onchange="filterTeams()" class="px-2.5 py-1.5 text-xs bg-[var(--card)] border border-[var(--border)] rounded-lg text-[var(--foreground)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]">
          <option value="all">All Tiers (1–5)</option>
          <option value="tier1_speed">Tier 1: Speed (&lt;200 lbs)</option>
          <option value="tier2_skill">Tier 2: Skill (200–224 lbs)</option>
          <option value="tier3_power">Tier 3: Power (225–254 lbs)</option>
          <option value="tier4_edge">Tier 4: Edge (255–289 lbs)</option>
          <option value="tier5_lineman">Tier 5: Lineman (290+ lbs)</option>
        </select>
      </div>
    </div>

    <!-- Team Quick Switcher -->
    <div class="mt-3 flex items-center gap-1.5 overflow-x-auto py-1 border-t border-[var(--border)] pt-3" id="team-pills">
      <!-- populated by JS -->
    </div>
  </header>

  <!-- Main Container -->
  <main class="max-w-7xl mx-auto">
    <div id="teams-container" class="space-y-8">
      <!-- Team Sections populated by JS -->
    </div>
  </main>

  <!-- Modal for Disqualification / Reason -->
  <div id="disqualify-modal" class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="bg-[var(--card)] border border-[var(--border)] rounded-2xl max-w-md w-full p-6 shadow-2xl">
      <h3 class="text-base font-bold text-[var(--foreground)] mb-1">Disqualify Donor</h3>
      <p class="text-xs text-[var(--muted-foreground)] mb-4" id="modal-player-desc"></p>

      <label class="block text-xs font-medium text-[var(--foreground)] mb-1.5">Disqualification Reason:</label>
      <div class="grid grid-cols-2 gap-2 mb-3" id="quick-reasons">
        <button onclick="setReason('Neck shaped weird / awkward slope')" class="p-2 text-xs border border-[var(--border)] rounded-lg text-left hover:bg-[var(--background)] transition">📐 Weird Neck Angle</button>
        <button onclick="setReason('Collar covered by dreads / hair')" class="p-2 text-xs border border-[var(--border)] rounded-lg text-left hover:bg-[var(--background)] transition">💇 Hair Obstructs Collar</button>
        <button onclick="setReason('Deep V-neck / collar sits too low')" class="p-2 text-xs border border-[var(--border)] rounded-lg text-left hover:bg-[var(--background)] transition">🎽 Deep V-Neck Cut</button>
        <button onclick="setReason('Uneven shoulder pads / crooked')" class="p-2 text-xs border border-[var(--border)] rounded-lg text-left hover:bg-[var(--background)] transition">🏈 Crooked Shoulders</button>
      </div>
      <input type="text" id="reason-input" placeholder="Or enter custom reason..." 
        class="w-full px-3 py-2 text-xs bg-[var(--background)] border border-[var(--border)] rounded-lg text-[var(--foreground)] mb-5 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]">

      <div class="flex items-center justify-end gap-2.5">
        <button onclick="closeModal()" class="px-4 py-2 text-xs font-medium border border-[var(--border)] rounded-lg hover:bg-[var(--background)] transition">Cancel</button>
        <button onclick="confirmDisqualify()" class="px-4 py-2 text-xs font-semibold bg-rose-600 text-white rounded-lg hover:bg-rose-700 transition">Disqualify & View Alternatives</button>
      </div>
    </div>
  </div>

  <!-- Modal for Exporting Decisions -->
  <div id="export-modal" class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="bg-[var(--card)] border border-[var(--border)] rounded-2xl max-w-xl w-full p-6 shadow-2xl">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-base font-bold text-[var(--foreground)]">Review Decisions Ledger</h3>
        <button onclick="closeExportModal()" class="text-[var(--muted-foreground)] hover:text-[var(--foreground)]">✕</button>
      </div>
      <p class="text-xs text-[var(--muted-foreground)] mb-4">
        Copy this ledger and send it in chat so Antigravity can automatically update the donor registry and download the alternative templates.
      </p>

      <textarea id="export-textarea" readonly class="w-full h-64 p-3 font-mono text-xs bg-[var(--background)] border border-[var(--border)] rounded-lg text-[var(--foreground)] focus:outline-none select-all mb-4"></textarea>

      <div class="flex items-center justify-between">
        <span class="text-xs text-[var(--muted-foreground)]" id="export-status"></span>
        <div class="flex items-center gap-2">
          <button onclick="copyExportText()" class="px-4 py-2 text-xs font-semibold bg-[var(--primary)] text-[var(--primary-foreground)] rounded-lg hover:opacity-90 transition">Copy to Clipboard</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Embedded Registry Script -->
  <script>
    const registry = {json.dumps(registry)};
    const divisions = {json.dumps(divisions)};
    const tierMeta = {json.dumps(tier_meta)};

    let currentDivision = 'All (32)';
    let currentTeam = 'NO';
    let searchQuery = '';
    let selectedTierFilter = 'all';

    // Track user swaps and disqualifications
    let userChanges = {{}};

    // Active modal state
    let targetModal = {{ team: null, tier: null }};

    function init() {{
      renderDivisionFilters();
      renderTeamPills();
      renderTeams();
    }}

    function renderDivisionFilters() {{
      const container = document.getElementById('div-filters');
      container.innerHTML = Object.keys(divisions).map(div => `
        <button onclick="setDivision('${{div}}')" class="px-2.5 py-1 rounded-md text-xs font-medium whitespace-nowrap transition ${{
          currentDivision === div ? 'bg-[var(--primary)] text-[var(--primary-foreground)]' : 'bg-[var(--card)] text-[var(--muted-foreground)] hover:text-[var(--foreground)] border border-[var(--border)]'
        }}">${{div}}</button>
      `).join('');
    }}

    function setDivision(div) {{
      currentDivision = div;
      renderDivisionFilters();
      const divTeams = divisions[div];
      if (divTeams && divTeams.length > 0 && !divTeams.includes(currentTeam)) {{
        currentTeam = divTeams[0];
      }}
      renderTeamPills();
      renderTeams();
    }}

    function renderTeamPills() {{
      const container = document.getElementById('team-pills');
      const divTeams = divisions[currentDivision] || Object.keys(registry);
      container.innerHTML = divTeams.map(t => `
        <button onclick="scrollToTeam('${{t}}')" class="px-3 py-1 rounded-lg text-xs font-semibold whitespace-nowrap transition flex items-center gap-1.5 border ${{
          currentTeam === t ? 'bg-[var(--foreground)] text-[var(--background)] border-[var(--foreground)] shadow-sm' : 'bg-[var(--card)] text-[var(--muted-foreground)] hover:text-[var(--foreground)] border-[var(--border)]'
        }}">
          <span>${{t}}</span>
          <span class="text-xs font-normal opacity-80">${{registry[t] ? registry[t].name.split(' ').pop() : ''}}</span>
        </button>
      `).join('');
    }}

    function scrollToTeam(team) {{
      currentTeam = team;
      renderTeamPills();
      const el = document.getElementById('team-' + team);
      if (el) {{
        el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      }}
    }}

    function filterTeams() {{
      searchQuery = document.getElementById('search-input').value.toLowerCase().trim();
      selectedTierFilter = document.getElementById('tier-filter').value;
      renderTeams();
    }}

    function renderTeams() {{
      const container = document.getElementById('teams-container');
      const divTeams = divisions[currentDivision] || Object.keys(registry);
      
      const filteredTeams = divTeams.filter(abbr => {{
        const team = registry[abbr];
        if (!team) return false;
        if (!searchQuery) return true;
        const matchTeam = team.name.toLowerCase().includes(searchQuery) || abbr.toLowerCase().includes(searchQuery);
        const matchPlayer = Object.values(team.tiers).some(t => t.player_name.toLowerCase().includes(searchQuery));
        return matchTeam || matchPlayer;
      }});

      if (filteredTeams.length === 0) {{
        container.innerHTML = `
          <div class="text-center py-16 bg-[var(--card)] border border-[var(--border)] rounded-2xl">
            <p class="text-sm text-[var(--muted-foreground)]">No teams or players found matching "${{searchQuery}}".</p>
          </div>
        `;
        return;
      }}

      container.innerHTML = filteredTeams.map(abbr => {{
        const team = registry[abbr];
        return `
          <div id="team-${{abbr}}" class="bg-[var(--card)] border border-[var(--border)] rounded-2xl p-5 shadow-sm">
            <div class="flex items-center justify-between border-b border-[var(--border)] pb-3 mb-5">
              <div class="flex items-center gap-3">
                <span class="text-lg font-black tracking-tight px-2.5 py-1 bg-[var(--background)] border border-[var(--border)] rounded-md">${{abbr}}</span>
                <div>
                  <h2 class="text-base font-bold text-[var(--foreground)]">${{team.name}}</h2>
                  <p class="text-xs text-[var(--muted-foreground)]">5 Physical Weight Brackets</p>
                </div>
              </div>
            </div>

            <!-- 5 Tiers Grid -->
            <div class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-4">
              ${{['tier1_speed', 'tier2_skill', 'tier3_power', 'tier4_edge', 'tier5_lineman'].map(tierKey => {{
                if (selectedTierFilter !== 'all' && selectedTierFilter !== tierKey) return '';
                const tier = team.tiers[tierKey];
                const meta = tierMeta[tierKey];
                if (!tier) return '';

                const isDisqualified = tier.isDisqualified || false;
                const changeKey = `${{abbr}}_${{tierKey}}`;
                const hasChange = userChanges[changeKey];

                return `
                  <div class="bg-[var(--background)] border border-[var(--border)] rounded-xl p-3.5 flex flex-col justify-between hover:border-[var(--foreground)]/30 transition">
                    <!-- Tier Header -->
                    <div>
                      <div class="flex items-center justify-between mb-1.5">
                        <span class="text-xs font-bold text-${{meta.color}}-400">${{meta.label}}</span>
                        <span class="text-xs font-mono px-1.5 py-0.5 rounded bg-[var(--card)] border border-[var(--border)] text-[var(--muted-foreground)]">${{meta.range}}</span>
                      </div>
                      <p class="text-xs text-[var(--muted-foreground)] line-clamp-1 mb-2.5">${{meta.desc}}</p>

                      <!-- Headshot Image Frame -->
                      <div class="relative aspect-square w-full rounded-lg overflow-hidden bg-white border border-[var(--border)] mb-3 flex items-center justify-center">
                        <img src="${{tier.headshot_url}}" alt="${{tier.player_name}}" class="donor-img w-full h-full object-cover object-top" loading="lazy" onerror="this.src='https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/default.png&w=400&h=400'">
                        
                        <!-- Collar Overlay Guide -->
                        <div class="absolute bottom-0 inset-x-0 h-10 bg-gradient-to-t from-black/50 to-transparent pointer-events-none flex items-end justify-center pb-1">
                          <span class="text-xs font-mono text-white/90">Collar Anchor</span>
                        </div>

                        ${{hasChange ? `
                          <div class="absolute top-2 right-2 px-1.5 py-0.5 rounded bg-blue-500 text-white text-xs font-bold shadow-md">SWAPPED</div>
                        ` : ''}}
                      </div>

                      <!-- Player Bio -->
                      <div class="mb-3">
                        <div class="flex items-center justify-between">
                          <h4 class="text-xs font-bold text-[var(--foreground)] truncate">${{tier.player_name}}</h4>
                          <span class="text-xs font-semibold text-[var(--muted-foreground)]">${{tier.position}}</span>
                        </div>
                        <div class="flex items-center justify-between text-xs text-[var(--muted-foreground)] mt-0.5">
                          <span>${{tier.height}} • ${{tier.weight}} lbs</span>
                          <span class="font-mono text-xs opacity-70">ID: ${{tier.espn_id}}</span>
                        </div>
                      </div>
                    </div>

                    <!-- Actions & Candidate Swapper -->
                    <div class="border-t border-[var(--border)] pt-2.5">
                      <div class="flex items-center gap-1.5 mb-2">
                        <button onclick="openDisqualifyModal('${{abbr}}', '${{tierKey}}', '${{tier.player_name}}', ${{tier.weight}})" 
                          class="flex-1 py-1.5 text-xs font-medium rounded-md border border-[var(--border)] hover:bg-rose-500/10 hover:border-rose-500/30 hover:text-rose-400 transition text-center">
                          Disqualify
                        </button>
                      </div>

                      <!-- Alternative Candidates Dropdown -->
                      <details class="group text-xs">
                        <summary class="cursor-pointer text-[var(--muted-foreground)] hover:text-[var(--foreground)] flex items-center justify-between py-1 select-none font-medium">
                          <span>Alternatives (${{tier.candidates ? tier.candidates.length : 0}})</span>
                          <span class="group-open:rotate-180 transition-transform">▾</span>
                        </summary>
                        <div class="mt-2 space-y-1.5 max-h-48 overflow-y-auto pr-1">
                          ${{(tier.candidates || []).map(cand => `
                            <div class="p-1.5 rounded-lg border border-[var(--border)] bg-[var(--card)] flex items-center justify-between gap-2 hover:border-[var(--primary)] transition">
                              <div class="flex items-center gap-1.5 min-w-0">
                                <img src="${{cand.headshot_url}}" class="w-7 h-7 rounded-full object-cover bg-white shrink-0" onerror="this.src='https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/default.png&w=100&h=100'">
                                <div class="min-w-0">
                                  <div class="text-xs font-semibold truncate">${{cand.name}}</div>
                                  <div class="text-xs text-[var(--muted-foreground)]">${{cand.position}} • ${{cand.weight}} lbs</div>
                                </div>
                              </div>
                              <button onclick="swapDonor('${{abbr}}', '${{tierKey}}', '${{cand.id}}')" 
                                class="px-2 py-1 text-xs font-bold bg-[var(--primary)] text-[var(--primary-foreground)] rounded hover:opacity-90 shrink-0">
                                Pick
                              </button>
                            </div>
                          `).join('')}}
                        </div>
                      </details>
                    </div>

                  </div>
                `;
              }}).join('')}}
            </div>
          </div>
        `;
      }}).join('');
    }}

    function openDisqualifyModal(team, tier, playerName, weight) {{
      targetModal = {{ team, tier }};
      document.getElementById('modal-player-desc').textContent = `${{playerName}} (${{weight}} lbs) — ${{team}} ${{tierMeta[tier].label}}`;
      document.getElementById('reason-input').value = '';
      document.getElementById('disqualify-modal').classList.remove('hidden');
    }}

    function closeModal() {{
      document.getElementById('disqualify-modal').classList.add('hidden');
    }}

    function setReason(r) {{
      document.getElementById('reason-input').value = r;
    }}

    function confirmDisqualify() {{
      const reason = document.getElementById('reason-input').value.trim() || 'Shaped weird / awkward collar';
      const {{ team, tier }} = targetModal;
      if (!team || !tier) return;

      const changeKey = `${{team}}_${{tier}}`;
      userChanges[changeKey] = {{
        action: 'disqualify',
        team,
        tier,
        old_player: registry[team].tiers[tier].player_name,
        old_id: registry[team].tiers[tier].espn_id,
        reason: reason
      }};

      updateChangeCount();
      closeModal();
      renderTeams();
    }}

    function swapDonor(team, tier, newId) {{
      const tierData = registry[team].tiers[tier];
      const newCand = tierData.candidates.find(c => c.id === newId);
      if (!newCand) return;

      const oldPlayer = tierData.player_name;
      const oldId = tierData.espn_id;

      // Update in local state
      tierData.espn_id = newCand.id;
      tierData.player_name = newCand.name;
      tierData.weight = newCand.weight;
      tierData.position = newCand.position;
      tierData.headshot_url = newCand.headshot_url;

      const changeKey = `${{team}}_${{tier}}`;
      userChanges[changeKey] = {{
        action: 'swap',
        team,
        tier,
        old_player: oldPlayer,
        old_id: oldId,
        new_player: newCand.name,
        new_id: newCand.id,
        new_weight: newCand.weight,
        new_pos: newCand.position
      }};

      updateChangeCount();
      renderTeams();
    }}

    function updateChangeCount() {{
      document.getElementById('change-count').textContent = Object.keys(userChanges).length;
    }}

    function exportDecisions() {{
      const payload = {{
        timestamp: new Date().toISOString(),
        total_swaps: Object.keys(userChanges).length,
        decisions: Object.values(userChanges)
      }};
      const jsonStr = JSON.stringify(payload, null, 2);
      document.getElementById('export-textarea').value = jsonStr;
      document.getElementById('export-status').textContent = `${{Object.keys(userChanges).length}} decisions ready to copy.`;
      document.getElementById('export-modal').classList.remove('hidden');
    }}

    function closeExportModal() {{
      document.getElementById('export-modal').classList.add('hidden');
    }}

    function copyExportText() {{
      const ta = document.getElementById('export-textarea');
      ta.select();
      navigator.clipboard.writeText(ta.value).then(() => {{
        document.getElementById('export-status').textContent = 'Copied to clipboard! Paste it into chat.';
      }});
    }}

    window.onload = init;
  </script>
</body>
</html>
"""

artifact_path = Path('/Users/jrereyi/.gemini/antigravity/brain/39fdfcbc-8ff1-441e-87aa-f503a60019df/donor_library_reviewer.html')
with open(artifact_path, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f"Generated standalone review studio at: {artifact_path}")
