"""Versioned fact-card rendering for the assigned-matchup Colab pilot.

Pure transformation of a frozen packet. Does not query databases or models.
"""
import copy
import json
import re


def decode(value):
    if isinstance(value,str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return copy.deepcopy(value)


def build_cards(packet):
    evidence=packet['evidence']
    sources={}
    def find(name, team=None):
        for index,e in enumerate(evidence):
            if e['source']==name and (team is None or e['arguments'].get('team_identifier')==team):
                ref=f'E{index:02d}'
                sources[ref]={'helper':name,'arguments':e['arguments']}
                return decode(e['result']),ref
        raise ValueError(f'Missing required evidence: {name} {team}')
    raw=packet['b']['task_instruction'].split('\n\nCANONICAL EVIDENCE DOSSIER:\n',1)[0]
    start=raw.index('{');brief,length=json.JSONDecoder().raw_decode(raw[start:])
    instructions=raw[start+length:]
    # Drop the tool-selection catalog; fact boundaries are carried in cards and
    # the final guard, while opening/voice/article structure remain intact.
    instructions=re.sub(r'CRITICAL BEAT WRITER RESEARCH & LOCAL REPORTING DIRECTIVE:.*?(?=BEAT WRITER OPENING VARIETY RULE:)', '', instructions, flags=re.S)
    week=packet['week']
    stages={19:'Wild Card playoff round',20:'Divisional playoff round',21:'Conference Championship playoff round',22:'Super Bowl bye week',23:'Super Bowl'}
    stage=stages.get(week,'regular season' if 1<=week<=18 else 'unclassified league week')
    preview=next((g for g in brief['preview_slate']['all_games'] if g['game_id']==packet['game_id']),None)
    if not preview:
        raise ValueError('Assigned game missing from preview slate')
    heading=f"ASSIGNMENT: Season {packet['season']}, publication Week {week}. Stage: {stage}. Game {packet['game_id']}: {preview['matchup']}. This game has NOT been played."
    if week==21:
        heading+=' The winner advances to the Super Bowl. The playoffs are already underway; this is NOT a pre-playoff game.'
    cards=[]
    for team in ['CAR',packet['opponent']]:
        profile,ref=find('get_team_efficiency_profile',team)
        situational=profile['situational']
        metric_fields=[('red_zone_td_pct','red_zone_rank','offensive red-zone TD conversion','%'),
                       ('red_zone_td_pct_allowed','red_zone_defense_rank','defensive red-zone TD allowed','%'),
                       ('third_down_pct','third_down_rank','offensive third-down conversion','%')]
        metrics=[dict(label=label,value=situational[value],unit=unit,league_rank=situational[rank],source=ref) for value,rank,label,unit in metric_fields]
        totals={k:profile[k] for k in ['record','points_for','points_against','pass_yds_per_game','rush_yds_per_game','turnover_margin']}
        cards.append(dict(kind='team',team=team,regular_season=totals,regular_season_through_week=situational['through_week'],metrics=metrics,rank_scale='1 is best; each rank belongs only to its own named metric',source=ref))
        roster,rref=find('get_team_roster_depth_chart',team)
        groups=roster['depth_chart_by_position_group']
        # Retain every named player/position once; do not guess starters from sort order.
        identities={}
        for players in groups.values():
            for player in players:
                name=player['name']
                if name in identities and identities[name]!=player['position']:
                    raise ValueError(f'Conflicting positions for {name}')
                identities[name]=player['position']
        cards.append(dict(kind='roster',team=team,players=identities,source=rref,note='Positions only; not a verified starter/depth order. Ratings intentionally omitted because publication directives prohibit them.'))
        for helper in ['get_coach_profile','get_team_injury_report','get_clutch_profile','get_team_season_schedule']:
            result,sref=find(helper,team)
            if helper=='get_team_season_schedule' and isinstance(result,dict):
                result={k:v for k,v in result.items() if k in {'team','season','record','one_score_record','completed_games','season_schedule'}}
                result['record_scope']='All visible completed games, regular season AND playoffs'
                result['season_schedule']=[{k:v for k,v in g.items() if k in {'week','game_id','matchup','outcome','status'}} for g in result['season_schedule']]
            elif helper=='get_clutch_profile' and isinstance(result,dict):
                fields={'games','record','fourth_quarter_comebacks','game_winning_drives','blown_fourth_quarter_leads','largest_comeback_win'}
                result={k:({f:v for f,v in values.items() if f in fields} if isinstance(values,dict) else values) for k,values in result.items() if k in {'team','season','regular_season','postseason'}}
            elif helper=='get_coach_profile' and isinstance(result,dict):
                for field in ['offensive_coordinator','defensive_coordinator']:
                    if isinstance(result.get(field),dict):
                        result[field]={k:v for k,v in result[field].items() if k in {'name','scheme','role'}}
                result.pop('staff_philosophy_summary',None)
            cards.append(dict(kind=helper,team=team,facts=result,source=sref))
        record,ref=find('get_record_book',team)
        categories={'season_rec_tds','season_rec_yards','season_pass_tds','season_pass_yards','season_sacks','season_team_points','season_team_wins'}
        cards.append(dict(kind='records',team=team,current_franchise_records=[r for r in record.get('franchise_records',[]) if r['category'] in categories],
                          current_season_events=[r for r in record.get('records_set_this_season',[]) if r['category'] in categories],source=ref,
                          note='Say tied when result=tied. Franchise record does not by itself establish an NFL record. Compare yardage and TD records separately.'))
    edges,ref=find('get_matchup_edges')
    cards.append(dict(kind='matchup_edges',facts=edges,source=ref))
    # The frozen packet already selects key players; keep their identity, awards and
    # regular/postseason lines, while dropping numerical ratings and popularity noise.
    for index,e in enumerate(evidence):
        helper=e['source']; result=decode(e['result']); ref=f'E{index:02d}'
        if helper=='get_player_context':
            keep={'player_id','name','position','team','age','years_of_experience','height','weight','career_awards','season_stats','games_played','postseason_stats','postseason_note','five_year_statline_summary'}
            sources[ref]={'helper':helper,'arguments':e['arguments']}
            cards.append(dict(kind='player',facts={k:v for k,v in result.items() if k in keep},source=ref))
        elif helper=='get_defense_special_teams_leaders':
            sources[ref]={'helper':helper,'arguments':e['arguments']}
            cards.append(dict(kind='defense_or_special_teams',facts=result,source=ref,note='These are category-selected leaders, not a full tackle leaderboard. Do not call a player the team tackle leader from this subset. Do not sum into team totals.'))
        elif helper=='get_game_box_score':
            sources[ref]={'helper':helper,'arguments':e['arguments']}
            cards.append(dict(kind='previous_game',facts=result,source=ref,note='This is a completed earlier playoff game, not the assigned upcoming game.' if week>19 else 'Completed game before the publication cutoff.'))
        elif helper=='get_player_scheme_splits' and isinstance(result,dict) and result.get('position')=='QB':
            # Keep sample counts and metric labels; remove fantasy/rushing clutter.
            for groups in result.get('splits_by_opponent',{}).values():
                for values in groups.values():
                    for field in ['per_game','vs_his_average']:
                        values[field]={k:v for k,v in values.get(field,{}).items() if k in {'pass_yards','pass_tds','interceptions','comp_pct','yards_per_attempt'}}
            sources[ref]={'helper':helper,'arguments':e['arguments']}
            cards.append(dict(kind='QB_splits',facts=result,source=ref,note='interceptions is interceptions PER GAME, not interception rate. Opponent strength is not measured pressure. Do not claim pressured/unpressured splits.'))
    # Preserve the entire league brief, without duplicating detailed subject game
    # narratives already provided as full previous-game cards.
    macro=copy.deepcopy(brief)
    for game in macro['recap_slate']['all_games']:
        game.pop('game_story',None)
    guard='''FACTUAL SELF-CHECK BEFORE FINAL COPY (do not print this checklist):
Check game stage, each player name/position, each number and its named metric/rank,
record scope and tied/broken status. Copy canonical values exactly or omit the claim.
Do not publish Madden/OVR ratings. Pressure counts, targets and snap counts are unknown;
do not invent them. Never turn a per-game statistic into a rate or a pressure split.
Creative quotes, private scenes and unrecorded plays are welcome if consistent with
canonical data. Preserve voice, varied angles and specific tactical interpretation.
Write the article, not a report about this dossier. No tool calls are available.
'''
    task='\n\n'.join([heading,'EDITORIAL ASSIGNMENT AND STYLE:'+instructions,
        'LEAGUE BRIEF (scores/standings/continuity):\n'+json.dumps(macro,separators=(',',':'),ensure_ascii=False),
        'SUBJECT FACT CARDS:\n'+json.dumps(cards,separators=(',',':'),ensure_ascii=False),heading,guard])
    return dict(task=task,assignment_header=heading,cards=cards,source_index=sources,
                omissions=['Optional cap/contracts/transactions/trade rumors/team-history helpers omitted; writer memory and league brief retained',
                           'OVR/popularity ratings and derived roster labels omitted; publication already forbids numerical OVR',
                           'Duplicate player-stat tool outputs and advanced-game analytics omitted; full box score/script retained',
                           'Non-QB scheme splits deferred','Detailed game_story duplicated in box scores removed from league recap brief',
                           'Tool-selection catalog replaced by fact cards and boundaries; article structure/voice retained',
                           'Schedule excitement/margin metadata and detailed clutch game lists deferred; scores, outcomes and scoped records retained'])
