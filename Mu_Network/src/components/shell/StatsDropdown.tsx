export function StatsDropdown({ close }: { close: () => void }) {
  return (
    <div className="stats-dropdown-wrap">
      <button className="mega-scrim" aria-label="Close stats menu" onClick={close} />
      <div className="stats-dropdown">
        <div className="stats-dropdown-title"><span className="eyebrow">League Data Center</span><h2>Explore Statistics</h2></div>
        <div className="stats-dropdown-options">
          <a href="#stats-team" onClick={close}>
            <span>01</span>
            <div><strong>Team Stats</strong><small>Compare all 32 clubs across offense, defense and special teams.</small></div>
            <i>→</i>
          </a>
          <a href="#stats-player" onClick={close}>
            <span>02</span>
            <div><strong>Player Stats</strong><small>League leaders, individual production and position rankings.</small></div>
            <i>→</i>
          </a>
        </div>
        <div className="stats-dropdown-group"><span className="eyebrow">History</span></div>
        <div className="stats-dropdown-options">
          <a href="#records" onClick={close}>
            <span>03</span>
            <div><strong>Record Book</strong><small>NFL records, franchise marks and the records set in this league.</small></div>
            <i>→</i>
          </a>
          <a href="#awards" onClick={close}>
            <span>04</span>
            <div><strong>Awards &amp; Champions</strong><small>Super Bowl champions, MVPs and year-end honors, and the All-Pro teams.</small></div>
            <i>→</i>
          </a>
        </div>
      </div>
    </div>
  );
}
