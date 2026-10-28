-- Agent Wire (docs/done/SOCIAL_MATRIX.md, build steps 1 and 2): social schema, account handles/names/bios,
-- the three new social accounts, and removal of the junk tweets saved before the "only real output is saved" fix.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_10_22_agent_wire_social.sql

BEGIN;

-- 1. Junk tweets: Gemini error strings and offline mock text (never real posts).
DELETE FROM news_feed
WHERE content_type = 'TWEET'
  AND (content LIKE 'Error calling Gemini%' OR content LIKE '[TWEET] Mock analysis%');

-- 2. Social columns on news_feed (posts stay in news_feed so agent memory, tagging and cascades keep working).
ALTER TABLE news_feed
    ADD COLUMN IF NOT EXISTS agent_id VARCHAR(100),            -- agent_memories.agent_id of the author
    ADD COLUMN IF NOT EXISTS post_kind VARCHAR(32),            -- REACTION | PERFORMANCE | BEAT | STANDINGS | SHARE | REPLY | PREVIEW | MEME
    ADD COLUMN IF NOT EXISTS posted_at TIMESTAMP,              -- simulated in-world posting time (created_at = real generation time)
    ADD COLUMN IF NOT EXISTS reply_to_id INT REFERENCES news_feed(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS linked_article_id INT REFERENCES news_feed(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS subject_game_id INT,              -- games.id the post is about
    ADD COLUMN IF NOT EXISTS subject_player_id INT,            -- players.id the post is about
    ADD COLUMN IF NOT EXISTS likes INT,
    ADD COLUMN IF NOT EXISTS reposts INT,
    ADD COLUMN IF NOT EXISTS reply_count INT,                  -- simulated replies shown on the card (real replies are added on read)
    ADD COLUMN IF NOT EXISTS views INT,
    ADD COLUMN IF NOT EXISTS hashtags TEXT[];

CREATE INDEX IF NOT EXISTS idx_news_feed_social_week ON news_feed (season, week, content_type, posted_at);
CREATE INDEX IF NOT EXISTS idx_news_feed_agent ON news_feed (agent_id);
CREATE INDEX IF NOT EXISTS idx_news_feed_reply_to ON news_feed (reply_to_id);

-- 3. Profile columns on agent_memories.
ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS bio VARCHAR(200),
    ADD COLUMN IF NOT EXISTS followers INT,                    -- set once by the social desk (§5); NULL until then
    ADD COLUMN IF NOT EXISTS avatar_color VARCHAR(10);         -- avatars are parked; filled later

-- 4. Weekly trends, computed from post tags and hashtags after each social run (§4).
CREATE TABLE IF NOT EXISTS social_trends (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,
    rank INT NOT NULL,
    kind VARCHAR(16) NOT NULL,          -- TEAM | PLAYER | GAME | HASHTAG
    key VARCHAR(64) NOT NULL,           -- team id, player id, game id or the hashtag
    title VARCHAR(120) NOT NULL,
    category VARCHAR(64),
    description TEXT,
    volume INT,                         -- simulated post count shown in the UI
    score DOUBLE PRECISION,             -- engagement-weighted ranking score
    UNIQUE (season, week, kind, key)
);
CREATE INDEX IF NOT EXISTS idx_social_trends_week ON social_trends (season, week, rank);

-- 5. New social accounts.
INSERT INTO agent_memories (agent_id, archetype, assigned_team_id, persistent_memory, handle, display_name, bio)
VALUES ('The_Flex_Spot', 'FANTASY_GURU', NULL, 'I am The Flex Spot, a fantasy football account. I live in the box score: volume, targets, red-zone work and who should be in your lineup this week.', '@TheFlexSpot', 'The Flex Spot', 'Full PPR, all season. Start/sit calls, waiver adds and the busts nobody wants to talk about.')
ON CONFLICT (agent_id) DO UPDATE SET handle = EXCLUDED.handle, display_name = EXCLUDED.display_name, bio = EXCLUDED.bio;
INSERT INTO agent_memories (agent_id, archetype, assigned_team_id, persistent_memory, handle, display_name, bio)
VALUES ('Gridiron_Memes', 'NFL_MEMES', NULL, 'I am Gridiron Memes, a viral NFL meme page. I roast losing teams, celebrate chaos and farm engagement from fan misery.', '@GridironMemes', 'Gridiron Memes', 'Your team lost? We noticed. NFL memes, every Sunday and every Monday after.')
ON CONFLICT (agent_id) DO UPDATE SET handle = EXCLUDED.handle, display_name = EXCLUDED.display_name, bio = EXCLUDED.bio;
INSERT INTO agent_memories (agent_id, archetype, assigned_team_id, persistent_memory, handle, display_name, bio)
VALUES ('Mock_Draft_Marty', 'DRAFT_SICKO', NULL, 'I am Mock Draft Marty. I care more about April than Sunday. Every loss is a better pick and every season ends with a mock draft.', '@MockDraftMarty', 'Mock Draft Marty', 'Mock draft 14.0 is up. Tanking is a strategy. April is the real season.')
ON CONFLICT (agent_id) DO UPDATE SET handle = EXCLUDED.handle, display_name = EXCLUDED.display_name, bio = EXCLUDED.bio;

-- 6. Handles, display names and bios for every account that posts (approved by JR, Oct 14, 2026).
UPDATE agent_memories SET handle = '@TheAthleticNFL', display_name = 'The Athletic Staff', bio = 'Process over narrative. Scheme breakdowns, roster trajectories and the work behind the results.' WHERE agent_id = 'The_Athletic_Staff';
UPDATE agent_memories SET handle = '@RealSkipBayless', display_name = 'Skip Bayless', bio = 'Long-form takes on clutch, rings and legacy.' WHERE agent_id = 'Skip_Bayless_National';
UPDATE agent_memories SET handle = '@billbarnwell', display_name = 'Bill Barnwell', bio = 'Regression is coming. Close-game records lie. Here are the numbers.' WHERE agent_id = 'Bill_Barnwell';
UPDATE agent_memories SET handle = '@SandoNFL', display_name = 'Mike Sando', bio = 'How front offices see the league: roster building, coaching and quarterback tiers.' WHERE agent_id = 'Mike_Sando';
UPDATE agent_memories SET handle = '@AdamSchefter', display_name = 'Adam Schefter', bio = 'NFL insider. Trades, signings and front-office news as it breaks.' WHERE agent_id = 'Schefter_Adam';
UPDATE agent_memories SET handle = '@stephenasmith', display_name = 'Stephen A. Smith', bio = 'I demand greatness. Mediocrity will be called out. Every. Single. Time.' WHERE agent_id = 'Stephen_A';
UPDATE agent_memories SET handle = '@RapSheet', display_name = 'Ian Rapoport', bio = 'NFL insider. The story behind the move, and the move before it happens.' WHERE agent_id = 'Rapoport_Ian';
UPDATE agent_memories SET handle = '@PatMcAfeeShow', display_name = 'Pat McAfee', bio = 'FOOTBALL IS ELECTRIC. Former punter. Professional enjoyer of this sport, dawg.' WHERE agent_id = 'Pat_McAfee';
UPDATE agent_memories SET handle = '@ShannonSharpe', display_name = 'Shannon Sharpe', bio = 'Hall of Fame tight end. Unbothered. Here to keep Skip honest 😂' WHERE agent_id = 'Shannon_Sharpe';
UPDATE agent_memories SET handle = '@RealSkipBayless', display_name = 'Skip Bayless', bio = 'Clutch gene believer. Rings over stats. I''ll be right when it matters.' WHERE agent_id = 'Skip_Bayless';
UPDATE agent_memories SET handle = '@ochocinco', display_name = 'Chad Johnson', bio = 'Route running is an art form. Child, please. 85 forever.' WHERE agent_id = 'Ochocinco';
UPDATE agent_memories SET handle = '@JJWatt', display_name = 'J.J. Watt', bio = 'Grown man football. Respect the trenches. Always.' WHERE agent_id = 'JJ_Watt';
UPDATE agent_memories SET handle = '@RGIII', display_name = 'Robert Griffin III', bio = 'Dual-threat QB truther. Puns are part of the game plan.' WHERE agent_id = 'RG3';
UPDATE agent_memories SET handle = '@KirkHerbstreit', display_name = 'Kirk Herbstreit', bio = 'Grateful for this game and everyone who plays it the right way.' WHERE agent_id = 'Kirk_Herbstreit';
UPDATE agent_memories SET handle = '@ColinCowherd', display_name = 'Colin Cowherd', bio = 'Quarterbacks are CEOs. Franchises are companies. Bet on leadership.' WHERE agent_id = 'Colin_Cowherd';
UPDATE agent_memories SET handle = '@MatthewBerryTMR', display_name = 'Matthew Berry', bio = 'Fantasy football storyteller. Every week, someone wins their league because of a player you overlooked.' WHERE agent_id = 'Matthew_Berry';
UPDATE agent_memories SET handle = '@wingoz', display_name = 'Trey Wingo', bio = 'Broadcaster. The present always makes more sense next to the past.' WHERE agent_id = 'Trey_Wingo';
UPDATE agent_memories SET handle = '@minakimes', display_name = 'Mina Kimes', bio = 'Scheme, numbers and the occasional dog photo. Mostly the dog.' WHERE agent_id = 'Mina_Kimes';
UPDATE agent_memories SET handle = '@Realrclark25', display_name = 'Ryan Clark', bio = 'Former safety. Players first. Front offices second.' WHERE agent_id = 'Ryan_Clark';
UPDATE agent_memories SET handle = '@danorlovsky7', display_name = 'Dan Orlovsky', bio = 'Quarterback play, frame by frame. Watch the feet.' WHERE agent_id = 'Dan_Orlovsky';
UPDATE agent_memories SET handle = '@richeisen', display_name = 'Rich Eisen', bio = 'Storyteller. The league''s history is the best show on television.' WHERE agent_id = 'Rich_Eisen';
UPDATE agent_memories SET handle = '@KCJoyner', display_name = 'KC Joyner', bio = 'Film charting and scheme efficiency. Ignore the noise.' WHERE agent_id = 'KC_Joyner';
UPDATE agent_memories SET handle = '@greggrosenthal', display_name = 'Gregg Rosenthal', bio = 'Football hipster. I watch every game so you don''t have to. Chaos teams welcome.' WHERE agent_id = 'Gregg_Rosenthal';
UPDATE agent_memories SET handle = '@jeffrichadiha', display_name = 'Jeffri Chadiha', bio = 'The human side of the game: leadership, legacy and the locker room.' WHERE agent_id = 'Jeffri_Chadiha';
UPDATE agent_memories SET handle = '@EmmanuelAcho', display_name = 'Emmanuel Acho', bio = 'Uncomfortable truths, said directly.' WHERE agent_id = 'Emmanuel_Acho';
UPDATE agent_memories SET handle = '@BillSimmons', display_name = 'Bill Simmons', bio = 'Everything is a 90s NBA comparison if you look hard enough.' WHERE agent_id = 'Bill_Simmons';
UPDATE agent_memories SET handle = '@FieldYates', display_name = 'Field Yates', bio = 'Lists. Facts. That''s it, that''s the bio.' WHERE agent_id = 'Field_Yates';
UPDATE agent_memories SET handle = '@Rachel_Perez', display_name = 'Rachel Perez', bio = 'Writing about the 49ers for a living, which means writing about the 49ers at all hours.' WHERE agent_id = '49ers_Beat_1';
UPDATE agent_memories SET handle = '@BrianBrownSF', display_name = 'Brian Brown', bio = 'San Francisco 49ers beat. If it happened at the facility, it''s in my notebook.' WHERE agent_id = '49ers_Beat_2';
UPDATE agent_memories SET handle = '@HannahOkaforSF', display_name = 'Hannah Okafor', bio = '49ers reporter. Every snap, every presser, every roster move.', persistent_memory = replace(persistent_memory, 'I am Ashley Perez,', 'I am Hannah Okafor,') WHERE agent_id = '49ers_Beat_3';
UPDATE agent_memories SET handle = '@TDavisCHI', display_name = 'Tom Davis', bio = 'Chicago Bears beat. If it happened at the facility, it''s in my notebook.' WHERE agent_id = 'Bears_Beat_1';
UPDATE agent_memories SET handle = '@ChrisSmithNFL', display_name = 'Chris Smith', bio = 'On the Bears beat. Film, locker room, front office. Tips welcome.' WHERE agent_id = 'Bears_Beat_2';
UPDATE agent_memories SET handle = '@AmandaMoore', display_name = 'Amanda Moore', bio = 'Bears reporter. Every snap, every presser, every roster move.' WHERE agent_id = 'Bears_Beat_3';
UPDATE agent_memories SET handle = '@AnthonyTaylorNFL', display_name = 'Anthony Taylor', bio = 'Writing about the Bengals for a living, which means writing about the Bengals at all hours.' WHERE agent_id = 'Bengals_Beat_1';
UPDATE agent_memories SET handle = '@MikeJackson', display_name = 'Mike Jackson', bio = 'On the Bills beat. Film, locker room, front office. Tips welcome.' WHERE agent_id = 'Bills_Beat_1';
UPDATE agent_memories SET handle = '@Caleb_Brennan', display_name = 'Caleb Brennan', bio = 'Writing about the Bills for a living, which means writing about the Bills at all hours.', persistent_memory = replace(persistent_memory, 'I am Mike Perez,', 'I am Caleb Brennan,') WHERE agent_id = 'Bills_Beat_2';
UPDATE agent_memories SET handle = '@Kimberly_Gonzalez', display_name = 'Kimberly Gonzalez', bio = 'Beat writer, Bills. Here for the depth chart, not the drama. Mostly.' WHERE agent_id = 'Bills_Beat_3';
UPDATE agent_memories SET handle = '@CamilaCastilloNFL', display_name = 'Camila Castillo', bio = 'Denver Broncos beat. If it happened at the facility, it''s in my notebook.', persistent_memory = replace(persistent_memory, 'I am Chris Taylor,', 'I am Camila Castillo,') WHERE agent_id = 'Broncos_Beat_1';
UPDATE agent_memories SET handle = '@VictorNakamuraNFL', display_name = 'Victor Nakamura', bio = 'On the Broncos beat. Film, locker room, front office. Tips welcome.', persistent_memory = replace(persistent_memory, 'I am Lauren Brown,', 'I am Victor Nakamura,') WHERE agent_id = 'Broncos_Beat_2';
UPDATE agent_memories SET handle = '@WhitfieldOnBroncos', display_name = 'Alicia Whitfield', bio = 'Writing about the Broncos for a living, which means writing about the Broncos at all hours.', persistent_memory = replace(persistent_memory, 'I am Brian Perez,', 'I am Alicia Whitfield,') WHERE agent_id = 'Broncos_Beat_3';
UPDATE agent_memories SET handle = '@TomLeeCLE', display_name = 'Tom Lee', bio = 'Writing about the Browns for a living, which means writing about the Browns at all hours.' WHERE agent_id = 'Browns_Beat_1';
UPDATE agent_memories SET handle = '@Brian_Rodriguez', display_name = 'Brian Rodriguez', bio = 'Inside the Buccaneers: practice reports, coaching decisions and what the numbers say.' WHERE agent_id = 'Buccaneers_Beat_1';
UPDATE agent_memories SET handle = '@Danielle_Abernathy', display_name = 'Danielle Abernathy', bio = 'Beat writer, Cardinals. Here for the depth chart, not the drama. Mostly.', persistent_memory = replace(persistent_memory, 'I am Mike Jackson,', 'I am Danielle Abernathy,') WHERE agent_id = 'Cardinals_Beat_1';
UPDATE agent_memories SET handle = '@MHernandezLAC', display_name = 'Marcus Hernandez', bio = 'Writing about the Chargers for a living, which means writing about the Chargers at all hours.' WHERE agent_id = 'Chargers_Beat_1';
UPDATE agent_memories SET handle = '@SarahWilsonNFL', display_name = 'Sarah Wilson', bio = 'Kansas City Chiefs beat. If it happened at the facility, it''s in my notebook.' WHERE agent_id = 'Chiefs_Beat_1';
UPDATE agent_memories SET handle = '@JDelgadoKC', display_name = 'Jordan Delgado', bio = 'On the Chiefs beat. Film, locker room, front office. Tips welcome.', persistent_memory = replace(persistent_memory, 'I am Anthony Brown,', 'I am Jordan Delgado,') WHERE agent_id = 'Chiefs_Beat_2';
UPDATE agent_memories SET handle = '@LaurenLopez', display_name = 'Lauren Lopez', bio = 'Chiefs reporter. Every snap, every presser, every roster move.' WHERE agent_id = 'Chiefs_Beat_3';
UPDATE agent_memories SET handle = '@WilliamsOnColts', display_name = 'Marcus Williams', bio = 'Beat writer, Colts. Here for the depth chart, not the drama. Mostly.' WHERE agent_id = 'Colts_Beat_1';
UPDATE agent_memories SET handle = '@Omar_Holloway', display_name = 'Omar Holloway', bio = 'Covering the Commanders from Commanders Field. Practice notes, depth-chart movement and the questions nobody else asks.', persistent_memory = replace(persistent_memory, 'I am Sarah Lopez,', 'I am Omar Holloway,') WHERE agent_id = 'Commanders_Beat_1';
UPDATE agent_memories SET handle = '@ThomasOnCowboys', display_name = 'Steven Thomas', bio = 'On the Cowboys beat. Film, locker room, front office. Tips welcome.' WHERE agent_id = 'Cowboys_Beat_1';
UPDATE agent_memories SET handle = '@JohnsonOnCowboys', display_name = 'Marcus Johnson', bio = 'Dallas Cowboys beat. If it happened at the facility, it''s in my notebook.' WHERE agent_id = 'Cowboys_Beat_2';
UPDATE agent_memories SET handle = '@LisaThompsonDAL', display_name = 'Lisa Thompson', bio = 'Cowboys reporter. Every snap, every presser, every roster move.' WHERE agent_id = 'Cowboys_Beat_3';
UPDATE agent_memories SET handle = '@RachelJones', display_name = 'Rachel Jones', bio = 'Writing about the Cowboys for a living, which means writing about the Cowboys at all hours.' WHERE agent_id = 'Cowboys_Beat_4';
UPDATE agent_memories SET handle = '@Priya_Prescott', display_name = 'Priya Prescott', bio = 'Beat writer, Cowboys. Here for the depth chart, not the drama. Mostly.', persistent_memory = replace(persistent_memory, 'I am Emily Hernandez,', 'I am Priya Prescott,') WHERE agent_id = 'Cowboys_Beat_5';
UPDATE agent_memories SET handle = '@Tessa_Lindqvist', display_name = 'Tessa Lindqvist', bio = 'On the Dolphins beat. Film, locker room, front office. Tips welcome.', persistent_memory = replace(persistent_memory, 'I am Nicole Brown,', 'I am Tessa Lindqvist,') WHERE agent_id = 'Dolphins_Beat_1';
UPDATE agent_memories SET handle = '@LaurenMercerPHI', display_name = 'Lauren Mercer', bio = 'Eagles reporter. Every snap, every presser, every roster move.', persistent_memory = replace(persistent_memory, 'I am Greg Thompson,', 'I am Lauren Mercer,') WHERE agent_id = 'Eagles_Beat_1';
UPDATE agent_memories SET handle = '@HannahAchebeATL', display_name = 'Hannah Achebe', bio = 'Falcons news and notes from Atlanta. Accountability first, access second.', persistent_memory = replace(persistent_memory, 'I am Anthony Davis,', 'I am Hannah Achebe,') WHERE agent_id = 'Falcons_Beat_1';
UPDATE agent_memories SET handle = '@BrookeDawsonNYG', display_name = 'Brooke Dawson', bio = 'Inside the Giants: practice reports, coaching decisions and what the numbers say.', persistent_memory = replace(persistent_memory, 'I am Paul Taylor,', 'I am Brooke Dawson,') WHERE agent_id = 'Giants_Beat_1';
UPDATE agent_memories SET handle = '@MartinezOnGiants', display_name = 'Paul Martinez', bio = 'Writing about the Giants for a living, which means writing about the Giants at all hours.' WHERE agent_id = 'Giants_Beat_2';
UPDATE agent_memories SET handle = '@LaurenFairbanksNYG', display_name = 'Lauren Fairbanks', bio = 'Covering the Giants from MetLife Stadium. Practice notes, depth-chart movement and the questions nobody else asks.', persistent_memory = replace(persistent_memory, 'I am Amanda Brown,', 'I am Lauren Fairbanks,') WHERE agent_id = 'Giants_Beat_3';
UPDATE agent_memories SET handle = '@TRamiresNYG', display_name = 'Tomas Ramires', bio = 'Giants reporter. Every snap, every presser, every roster move.', persistent_memory = replace(persistent_memory, 'I am Marcus Gonzalez,', 'I am Tomas Ramires,') WHERE agent_id = 'Giants_Beat_4';
UPDATE agent_memories SET handle = '@PriyaSuttonNFL', display_name = 'Priya Sutton', bio = 'Beat writer, Giants. Here for the depth chart, not the drama. Mostly.', persistent_memory = replace(persistent_memory, 'I am Lauren Lee,', 'I am Priya Sutton,') WHERE agent_id = 'Giants_Beat_5';
UPDATE agent_memories SET handle = '@RKowalskiJAX', display_name = 'Renee Kowalski', bio = 'Covering the Jaguars from EverBank Stadium. Practice notes, depth-chart movement and the questions nobody else asks.', persistent_memory = replace(persistent_memory, 'I am Tom Thomas,', 'I am Renee Kowalski,') WHERE agent_id = 'Jaguars_Beat_1';
UPDATE agent_memories SET handle = '@IsaacIngram', display_name = 'Isaac Ingram', bio = 'Inside the Jets: practice reports, coaching decisions and what the numbers say.', persistent_memory = replace(persistent_memory, 'I am Mike Taylor,', 'I am Isaac Ingram,') WHERE agent_id = 'Jets_Beat_1';
UPDATE agent_memories SET handle = '@VanceOnLions', display_name = 'Luis Vance', bio = 'Lions reporter. Every snap, every presser, every roster move.', persistent_memory = replace(persistent_memory, 'I am Melissa Taylor,', 'I am Luis Vance,') WHERE agent_id = 'Lions_Beat_1';
UPDATE agent_memories SET handle = '@Malik_Haddad', display_name = 'Malik Haddad', bio = 'On the Packers beat. Film, locker room, front office. Tips welcome.', persistent_memory = replace(persistent_memory, 'I am Sarah Brown,', 'I am Malik Haddad,') WHERE agent_id = 'Packers_Beat_1';
UPDATE agent_memories SET handle = '@AliciaGallagherCAR', display_name = 'Alicia Gallagher', bio = 'Panthers news and notes from Carolina. Accountability first, access second.', persistent_memory = replace(persistent_memory, 'I am Emily Brown,', 'I am Alicia Gallagher,') WHERE agent_id = 'Panthers_Beat_1';
UPDATE agent_memories SET handle = '@CamilaPruitt', display_name = 'Camila Pruitt', bio = 'On the Panthers beat. Film, locker room, front office. Tips welcome.', persistent_memory = replace(persistent_memory, 'I am Jason Taylor,', 'I am Camila Pruitt,') WHERE agent_id = 'Panthers_Beat_2';
UPDATE agent_memories SET handle = '@Grant_Moreau', display_name = 'Grant Moreau', bio = 'Beat writer, Panthers. Here for the depth chart, not the drama. Mostly.', persistent_memory = replace(persistent_memory, 'I am Nicole Rodriguez,', 'I am Grant Moreau,') WHERE agent_id = 'Panthers_Beat_3';
UPDATE agent_memories SET handle = '@GAndersonNE', display_name = 'Greg Anderson', bio = 'Covering the Patriots from Gillette Stadium. Practice notes, depth-chart movement and the questions nobody else asks.' WHERE agent_id = 'Patriots_Beat_1';
UPDATE agent_memories SET handle = '@Tessa_Tran', display_name = 'Tessa Tran', bio = 'On the Patriots beat. Film, locker room, front office. Tips welcome.', persistent_memory = replace(persistent_memory, 'I am Brian Lopez,', 'I am Tessa Tran,') WHERE agent_id = 'Patriots_Beat_2';
UPDATE agent_memories SET handle = '@ShannonBeckettNE', display_name = 'Shannon Beckett', bio = 'Beat writer, Patriots. Here for the depth chart, not the drama. Mostly.', persistent_memory = replace(persistent_memory, 'I am Nicole Jones,', 'I am Shannon Beckett,') WHERE agent_id = 'Patriots_Beat_3';
UPDATE agent_memories SET handle = '@LMartinLV', display_name = 'Lisa Martin', bio = 'Beat writer, Raiders. Here for the depth chart, not the drama. Mostly.' WHERE agent_id = 'Raiders_Beat_1';
UPDATE agent_memories SET handle = '@OyelaranOnRaiders', display_name = 'Jordan Oyelaran', bio = 'Raiders reporter. Every snap, every presser, every roster move.', persistent_memory = replace(persistent_memory, 'I am Greg Perez,', 'I am Jordan Oyelaran,') WHERE agent_id = 'Raiders_Beat_2';
UPDATE agent_memories SET handle = '@RachelStroudNFL', display_name = 'Rachel Stroud', bio = 'Writing about the Raiders for a living, which means writing about the Raiders at all hours.', persistent_memory = replace(persistent_memory, 'I am Amanda Anderson,', 'I am Rachel Stroud,') WHERE agent_id = 'Raiders_Beat_3';
UPDATE agent_memories SET handle = '@CalebWhitakerLAR', display_name = 'Caleb Whitaker', bio = 'Writing about the Rams for a living, which means writing about the Rams at all hours.', persistent_memory = replace(persistent_memory, 'I am Lauren Lopez,', 'I am Caleb Whitaker,') WHERE agent_id = 'Rams_Beat_1';
UPDATE agent_memories SET handle = '@PriyaIversonBAL', display_name = 'Priya Iverson', bio = 'Ravens reporter. Every snap, every presser, every roster move.', persistent_memory = replace(persistent_memory, 'I am Nicole Lopez,', 'I am Priya Iverson,') WHERE agent_id = 'Ravens_Beat_1';
UPDATE agent_memories SET handle = '@WCallowayBAL', display_name = 'Wesley Calloway', bio = 'Ravens news and notes from Baltimore. Accountability first, access second.', persistent_memory = replace(persistent_memory, 'I am Emily Williams,', 'I am Wesley Calloway,') WHERE agent_id = 'Ravens_Beat_2';
UPDATE agent_memories SET handle = '@Marcus_Novak', display_name = 'Marcus Novak', bio = 'Writing about the Ravens for a living, which means writing about the Ravens at all hours.', persistent_memory = replace(persistent_memory, 'I am Sarah Taylor,', 'I am Marcus Novak,') WHERE agent_id = 'Ravens_Beat_3';
UPDATE agent_memories SET handle = '@KevinEscobar', display_name = 'Kevin Escobar', bio = 'Covering the Saints from Caesars Superdome. Practice notes, depth-chart movement and the questions nobody else asks.', persistent_memory = replace(persistent_memory, 'I am Mike Jackson,', 'I am Kevin Escobar,') WHERE agent_id = 'Saints_Beat_1';
UPDATE agent_memories SET handle = '@HartleyOnSeahawks', display_name = 'Marcus Hartley', bio = 'Seahawks reporter. Every snap, every presser, every roster move.', persistent_memory = replace(persistent_memory, 'I am Mike Thompson,', 'I am Marcus Hartley,') WHERE agent_id = 'Seahawks_Beat_1';
UPDATE agent_memories SET handle = '@Marcus_Miller', display_name = 'Marcus Miller', bio = 'Writing about the Seahawks for a living, which means writing about the Seahawks at all hours.' WHERE agent_id = 'Seahawks_Beat_2';
UPDATE agent_memories SET handle = '@TessaQuinlanSEA', display_name = 'Tessa Quinlan', bio = 'Covering the Seahawks from Lumen Field. Practice notes, depth-chart movement and the questions nobody else asks.', persistent_memory = replace(persistent_memory, 'I am Jessica Lee,', 'I am Tessa Quinlan,') WHERE agent_id = 'Seahawks_Beat_3';
UPDATE agent_memories SET handle = '@LuisRourkeNFL', display_name = 'Luis Rourke', bio = 'Writing about the Steelers for a living, which means writing about the Steelers at all hours.', persistent_memory = replace(persistent_memory, 'I am Lisa Lopez,', 'I am Luis Rourke,') WHERE agent_id = 'Steelers_Beat_1';
UPDATE agent_memories SET handle = '@JordanSalazar', display_name = 'Jordan Salazar', bio = 'Covering the Steelers from Acrisure Stadium. Practice notes, depth-chart movement and the questions nobody else asks.', persistent_memory = replace(persistent_memory, 'I am Steven Jackson,', 'I am Jordan Salazar,') WHERE agent_id = 'Steelers_Beat_2';
UPDATE agent_memories SET handle = '@Amanda_White', display_name = 'Amanda White', bio = 'On the Steelers beat. Film, locker room, front office. Tips welcome.' WHERE agent_id = 'Steelers_Beat_3';
UPDATE agent_memories SET handle = '@KAdeyemiHOU', display_name = 'Kelsey Adeyemi', bio = 'Texans news and notes from Houston. Accountability first, access second.', persistent_memory = replace(persistent_memory, 'I am Melissa Martin,', 'I am Kelsey Adeyemi,') WHERE agent_id = 'Texans_Beat_1';
UPDATE agent_memories SET handle = '@Brooke_Barrett', display_name = 'Brooke Barrett', bio = 'Covering the Texans from NRG Stadium. Practice notes, depth-chart movement and the questions nobody else asks.', persistent_memory = replace(persistent_memory, 'I am Melissa Jackson,', 'I am Brooke Barrett,') WHERE agent_id = 'Texans_Beat_2';
UPDATE agent_memories SET handle = '@BChangHOU', display_name = 'Brooke Chang', bio = 'Houston Texans beat. If it happened at the facility, it''s in my notebook.', persistent_memory = replace(persistent_memory, 'I am Rachel Lopez,', 'I am Brooke Chang,') WHERE agent_id = 'Texans_Beat_3';
UPDATE agent_memories SET handle = '@ErinDonnellyNFL', display_name = 'Erin Donnelly', bio = 'Covering the Titans from Nissan Stadium. Practice notes, depth-chart movement and the questions nobody else asks.', persistent_memory = replace(persistent_memory, 'I am Marcus Williams,', 'I am Erin Donnelly,') WHERE agent_id = 'Titans_Beat_1';
UPDATE agent_memories SET handle = '@GrantEllisonMIN', display_name = 'Grant Ellison', bio = 'Vikings reporter. Every snap, every presser, every roster move.', persistent_memory = replace(persistent_memory, 'I am Ashley Martinez,', 'I am Grant Ellison,') WHERE agent_id = 'Vikings_Beat_1';
UPDATE agent_memories SET handle = '@AndreFerreiraMIN', display_name = 'Andre Ferreira', bio = 'Writing about the Vikings for a living, which means writing about the Vikings at all hours.', persistent_memory = replace(persistent_memory, 'I am Mike Wilson,', 'I am Andre Ferreira,') WHERE agent_id = 'Vikings_Beat_2';
UPDATE agent_memories SET handle = '@NatalieGutierrez', display_name = 'Natalie Gutierrez', bio = 'On the Vikings beat. Film, locker room, front office. Tips welcome.', persistent_memory = replace(persistent_memory, 'I am Ashley Martinez,', 'I am Natalie Gutierrez,') WHERE agent_id = 'Vikings_Beat_3';
UPDATE agent_memories SET handle = '@12s_Trey56', display_name = 'Trey 💙', bio = 'Watched every Seahawks snap since I could walk. Casuals, unfollow now.' WHERE agent_id = '@12s_Trey56';
UPDATE agent_memories SET handle = '@Seahawks_Nick67', display_name = 'Nick 💚', bio = 'Bleeding Seattle colors since day one. Ask me about the refs.' WHERE agent_id = '@Seahawks_Nick67';
UPDATE agent_memories SET handle = '@Seahawks_Zack25', display_name = 'Zack 💙', bio = 'Seattle raised. If you''re not with the Seahawks, you''re against us.' WHERE agent_id = '@Seahawks_Zack25';
UPDATE agent_memories SET handle = '@Arrowhead_Dom49', display_name = 'Dom 💛', bio = 'Bleeding Kansas City colors since day one. Ask me about the refs.' WHERE agent_id = '@Arrowhead_Dom49';
UPDATE agent_memories SET handle = '@ChiefsKingdom_Tre97', display_name = 'Tre 🏹', bio = 'Tailgate captain. GEHA Field at Arrowhead regular. Homer and proud 🏹', persistent_memory = replace(persistent_memory, 'I am Dom 🏹.', 'I am Tre 🏹.') WHERE agent_id = '@ChiefsKingdom_Dom97';
UPDATE agent_memories SET handle = '@Chiefs_Mike73', display_name = 'Mike 💛', bio = 'Chiefs fan since birth. GEHA Field at Arrowhead is my church. 💛' WHERE agent_id = '@Chiefs_Mike73';
UPDATE agent_memories SET handle = '@BearDown_Mike14', display_name = 'Mike ⬇️', bio = 'Season tickets, bad takes, zero neutrality. Bears forever ⬇️' WHERE agent_id = '@BearDown_Mike14';
UPDATE agent_memories SET handle = '@BearDown_Nick64', display_name = 'Nick 🐻', bio = 'Bears fan since birth. Soldier Field is my church. 🐻' WHERE agent_id = '@BearDown_Nick64';
UPDATE agent_memories SET handle = '@DaBears_Sam79', display_name = 'Sam 🏈', bio = 'Bleeding Chicago colors since day one. Ask me about the refs.' WHERE agent_id = '@DaBears_Sam79';
UPDATE agent_memories SET handle = '@BigBlue_Dom47', display_name = 'Dom 🏈', bio = 'Giants or nothing. In the parking lot by 8am every home game.' WHERE agent_id = '@BigBlue_Dom47';
UPDATE agent_memories SET handle = '@NYG_Nick25', display_name = 'Nick 🗽', bio = 'Season tickets, bad takes, zero neutrality. Giants forever 🗽' WHERE agent_id = '@NYG_Nick25';
UPDATE agent_memories SET handle = '@NYG_Ty25', display_name = 'Ty 🗽', bio = 'Never left during the bad years. Not leaving now. 🗽' WHERE agent_id = '@NYG_Ty25';
UPDATE agent_memories SET handle = '@TogetherBlue_Dave46', display_name = 'Dave 🗽', bio = 'Giants fan since birth. MetLife Stadium is my church. 🗽' WHERE agent_id = '@TogetherBlue_Dave46';
UPDATE agent_memories SET handle = '@BirdCity_Nick14', display_name = 'Nick 🏜️', bio = 'Cardinals fan since birth. State Farm Stadium is my church. 🏜️' WHERE agent_id = '@BirdCity_Nick14';
UPDATE agent_memories SET handle = '@RedSea_Matt96', display_name = 'Matt 🦅', bio = 'My mood is tied directly to the Cardinals'' record. Apologies in advance.' WHERE agent_id = '@RedSea_Matt96';
UPDATE agent_memories SET handle = '@BirdGang_Jay67', display_name = 'Jay 😤', bio = 'Watched every Eagles snap since I could walk. Casuals, unfollow now.' WHERE agent_id = '@BirdGang_Jay67';
UPDATE agent_memories SET handle = '@FlyEaglesFly_Rob35', display_name = 'Rob 🦅', bio = 'Eagles fan since birth. Lincoln Financial Field is my church. 🦅' WHERE agent_id = '@FlyEaglesFly_Rob35';
UPDATE agent_memories SET handle = '@Buffalo_Tom54', display_name = 'Tom ❤️', bio = 'Born into it. Buffalo till I die. Don''t @ me with your regression stats.' WHERE agent_id = '@Buffalo_Tom54';
UPDATE agent_memories SET handle = '@GoBills_Mike59', display_name = 'Mike 🦬', bio = 'My mood is tied directly to the Bills'' record. Apologies in advance.' WHERE agent_id = '@GoBills_Mike59';
UPDATE agent_memories SET handle = '@GoBills_Ty99', display_name = 'Ty 🦬', bio = 'Season tickets, bad takes, zero neutrality. Bills forever 🦬' WHERE agent_id = '@GoBills_Ty99';
UPDATE agent_memories SET handle = '@Carolina_Matt48', display_name = 'Matt 🐾', bio = 'Never left during the bad years. Not leaving now. 🐾' WHERE agent_id = '@Carolina_Matt48';
UPDATE agent_memories SET handle = '@Carolina_Jalen59', display_name = 'Jalen 🐾', bio = 'Panthers fan since birth. Bank of America Stadium is my church. 🐾', persistent_memory = replace(persistent_memory, 'I am Matt 🐾.', 'I am Jalen 🐾.') WHERE agent_id = '@Carolina_Matt59';
UPDATE agent_memories SET handle = '@Panthers_Dave26', display_name = 'Dave 🐾', bio = 'Born into it. Carolina till I die. Don''t @ me with your regression stats.' WHERE agent_id = '@Panthers_Dave26';
UPDATE agent_memories SET handle = '@Chargers_Josh13', display_name = 'Josh ⚡', bio = 'Never left during the bad years. Not leaving now. ⚡' WHERE agent_id = '@Chargers_Josh13';
UPDATE agent_memories SET handle = '@Chargers_Marcus11', display_name = 'Marcus ⚡', bio = 'Chargers fan since birth. SoFi Stadium is my church. ⚡' WHERE agent_id = '@Chargers_Marcus11';
UPDATE agent_memories SET handle = '@Cleveland_Dom10', display_name = 'Dom 🟤', bio = 'Season tickets, bad takes, zero neutrality. Browns forever 🟤' WHERE agent_id = '@Cleveland_Dom10';
UPDATE agent_memories SET handle = '@Cleveland_Mike92', display_name = 'Mike 🟠', bio = 'Cleveland raised. If you''re not with the Browns, you''re against us.' WHERE agent_id = '@Cleveland_Mike92';
UPDATE agent_memories SET handle = '@Commanders_Sam32', display_name = 'Sam 🛡️', bio = 'Washington raised. If you''re not with the Commanders, you''re against us.' WHERE agent_id = '@Commanders_Sam32';
UPDATE agent_memories SET handle = '@DC_Josh71', display_name = 'Josh 🔥', bio = 'Bleeding Washington colors since day one. Ask me about the refs.' WHERE agent_id = '@DC_Josh71';
UPDATE agent_memories SET handle = '@CowboysNation_Dan81', display_name = 'Dan 🤠', bio = 'Born into it. Dallas till I die. Don''t @ me with your regression stats.' WHERE agent_id = '@CowboysNation_Dan81';
UPDATE agent_memories SET handle = '@DC4L_Tom52', display_name = 'Tom 🤠', bio = 'Cowboys fan since birth. AT&T Stadium is my church. 🤠' WHERE agent_id = '@DC4L_Tom52';
UPDATE agent_memories SET handle = '@DC4L_Zack73', display_name = 'Zack ✭', bio = 'Season tickets, bad takes, zero neutrality. Cowboys forever ✭' WHERE agent_id = '@DC4L_Zack73';
UPDATE agent_memories SET handle = '@DemBoyz_Alex90', display_name = 'Alex 🏈', bio = 'Dallas raised. If you''re not with the Cowboys, you''re against us.' WHERE agent_id = '@DemBoyz_Alex90';
UPDATE agent_memories SET handle = '@DefendTheDen_Anthony94', display_name = 'Anthony 🔥', bio = 'My mood is tied directly to the Lions'' record. Apologies in advance.' WHERE agent_id = '@DefendTheDen_Anthony94';
UPDATE agent_memories SET handle = '@Lions_Jay57', display_name = 'Jay 🔥', bio = 'Detroit raised. If you''re not with the Lions, you''re against us.' WHERE agent_id = '@Lions_Jay57';
UPDATE agent_memories SET handle = '@DenverBroncos_Josh26', display_name = 'Josh 🐴', bio = 'Born into it. Denver till I die. Don''t @ me with your regression stats.' WHERE agent_id = '@DenverBroncos_Josh26';
UPDATE agent_memories SET handle = '@DenverBroncos_Mike83', display_name = 'Mike 🐴', bio = 'Bleeding Denver colors since day one. Ask me about the refs.' WHERE agent_id = '@DenverBroncos_Mike83';
UPDATE agent_memories SET handle = '@DenverBroncos_Quinn88', display_name = 'Quinn 🐴', bio = 'Broncos fan since birth. Empower Field at Mile High is my church. 🐴', persistent_memory = replace(persistent_memory, 'I am Mike 🐴.', 'I am Quinn 🐴.') WHERE agent_id = '@DenverBroncos_Mike88';
UPDATE agent_memories SET handle = '@FTTB_Trey63', display_name = 'Trey ❤️', bio = 'Bleeding San Francisco colors since day one. Ask me about the refs.' WHERE agent_id = '@FTTB_Trey63';
UPDATE agent_memories SET handle = '@NinerEmpire_Sam55', display_name = 'Sam ❤️', bio = 'My mood is tied directly to the 49ers'' record. Apologies in advance.' WHERE agent_id = '@NinerEmpire_Sam55';
UPDATE agent_memories SET handle = '@RedAndGold_Josh67', display_name = 'Josh ⛏️', bio = '49ers fan since birth. Levi''s Stadium is my church. ⛏️' WHERE agent_id = '@RedAndGold_Josh67';
UPDATE agent_memories SET handle = '@ForTheShoe_Rob60', display_name = 'Rob 💙', bio = 'My mood is tied directly to the Colts'' record. Apologies in advance.' WHERE agent_id = '@ForTheShoe_Rob60';
UPDATE agent_memories SET handle = '@ForTheShoe_Zack18', display_name = 'Zack 🐴', bio = 'Bleeding Indianapolis colors since day one. Ask me about the refs.' WHERE agent_id = '@ForTheShoe_Zack18';
UPDATE agent_memories SET handle = '@ForeverNE_Marcus96', display_name = 'Marcus ❤️', bio = 'Patriots or nothing. In the parking lot by 8am every home game.' WHERE agent_id = '@ForeverNE_Marcus96';
UPDATE agent_memories SET handle = '@Patriots_Jay25', display_name = 'Jay 💙', bio = 'Season tickets, bad takes, zero neutrality. Patriots forever 💙' WHERE agent_id = '@Patriots_Jay25';
UPDATE agent_memories SET handle = '@PatsNation_Dom98', display_name = 'Dom 💙', bio = 'Tailgate captain. Gillette Stadium regular. Homer and proud 💙' WHERE agent_id = '@PatsNation_Dom98';
UPDATE agent_memories SET handle = '@GoBucs_Rob81', display_name = 'Rob 🏴‍☠️', bio = 'Tampa Bay raised. If you''re not with the Buccaneers, you''re against us.' WHERE agent_id = '@GoBucs_Rob81';
UPDATE agent_memories SET handle = '@Krewe_Nick52', display_name = 'Nick 🏴‍☠️', bio = 'Buccaneers or nothing. In the parking lot by 8am every home game.' WHERE agent_id = '@Krewe_Nick52';
UPDATE agent_memories SET handle = '@GoPackGo_Sam36', display_name = 'Sam 💚', bio = 'Packers or nothing. In the parking lot by 8am every home game.' WHERE agent_id = '@GoPackGo_Sam36';
UPDATE agent_memories SET handle = '@Packers_Jay68', display_name = 'Jay 💚', bio = 'Tailgate captain. Lambeau Field regular. Homer and proud 💚' WHERE agent_id = '@Packers_Jay68';
UPDATE agent_memories SET handle = '@HereWeGo_Dan46', display_name = 'Dan 🖤', bio = 'Steelers or nothing. In the parking lot by 8am every home game.' WHERE agent_id = '@HereWeGo_Dan46';
UPDATE agent_memories SET handle = '@HereWeGo_Jay95', display_name = 'Jay 🖤', bio = 'Never left during the bad years. Not leaving now. 🖤' WHERE agent_id = '@HereWeGo_Jay95';
UPDATE agent_memories SET handle = '@HereWeGo_Nick15', display_name = 'Nick 💛', bio = 'Tailgate captain. Acrisure Stadium regular. Homer and proud 💛' WHERE agent_id = '@HereWeGo_Nick15';
UPDATE agent_memories SET handle = '@Houston_Matt90', display_name = 'Matt 💙', bio = 'Texans or nothing. In the parking lot by 8am every home game.' WHERE agent_id = '@Houston_Matt90';
UPDATE agent_memories SET handle = '@Houston_Zack82', display_name = 'Zack 💙', bio = 'Bleeding Houston colors since day one. Ask me about the refs.' WHERE agent_id = '@Houston_Zack82';
UPDATE agent_memories SET handle = '@WeAreTexans_Sam82', display_name = 'Sam 💙', bio = 'Never left during the bad years. Not leaving now. 💙' WHERE agent_id = '@WeAreTexans_Sam82';
UPDATE agent_memories SET handle = '@Jaguars_Dan18', display_name = 'Dan 🏈', bio = 'Bleeding Jacksonville colors since day one. Ask me about the refs.' WHERE agent_id = '@Jaguars_Dan18';
UPDATE agent_memories SET handle = '@Jax_Rob69', display_name = 'Rob 🐆', bio = 'Never left during the bad years. Not leaving now. 🐆' WHERE agent_id = '@Jax_Rob69';
UPDATE agent_memories SET handle = '@Jets_Trey56', display_name = 'Trey 💚', bio = 'Bleeding New York colors since day one. Ask me about the refs.' WHERE agent_id = '@Jets_Trey56';
UPDATE agent_memories SET handle = '@NYJ_Dom81', display_name = 'Dom 💚', bio = 'Never left during the bad years. Not leaving now. 💚' WHERE agent_id = '@NYJ_Dom81';
UPDATE agent_memories SET handle = '@JustWinBaby_Tom79', display_name = 'Tom 🏴‍☠️', bio = 'Season tickets, bad takes, zero neutrality. Raiders forever 🏴‍☠️' WHERE agent_id = '@JustWinBaby_Tom79';
UPDATE agent_memories SET handle = '@LasVegasRaiders_Alex83', display_name = 'Alex 🖤', bio = 'My mood is tied directly to the Raiders'' record. Apologies in advance.' WHERE agent_id = '@LasVegasRaiders_Alex83';
UPDATE agent_memories SET handle = '@LasVegasRaiders_Sam42', display_name = 'Sam 🏴‍☠️', bio = 'Bleeding Las Vegas colors since day one. Ask me about the refs.' WHERE agent_id = '@LasVegasRaiders_Sam42';
UPDATE agent_memories SET handle = '@LARams_Sam50', display_name = 'Sam 💙', bio = 'Bleeding Los Angeles colors since day one. Ask me about the refs.' WHERE agent_id = '@LARams_Sam50';
UPDATE agent_memories SET handle = '@RamsHouse_Nick63', display_name = 'Nick 💛', bio = 'Rams fan since birth. SoFi Stadium is my church. 💛' WHERE agent_id = '@RamsHouse_Nick63';
UPDATE agent_memories SET handle = '@Phins_Dave47', display_name = 'Dave 🐬', bio = 'Dolphins fan since birth. Hard Rock Stadium is my church. 🐬' WHERE agent_id = '@Phins_Dave47';
UPDATE agent_memories SET handle = '@Phins_Marcus93', display_name = 'Marcus 🩵', bio = 'Season tickets, bad takes, zero neutrality. Dolphins forever 🩵' WHERE agent_id = '@Phins_Marcus93';
UPDATE agent_memories SET handle = '@PlayLikeARaven_Chris27', display_name = 'Chris 🖤', bio = 'Never left during the bad years. Not leaving now. 🖤' WHERE agent_id = '@PlayLikeARaven_Chris27';
UPDATE agent_memories SET handle = '@RavensFlock_Trey22', display_name = 'Trey 🖤', bio = 'Season tickets, bad takes, zero neutrality. Ravens forever 🖤' WHERE agent_id = '@RavensFlock_Trey22';
UPDATE agent_memories SET handle = '@RavensFlock_Zack99', display_name = 'Zack 🐦‍⬛', bio = 'Ravens fan since birth. M&T Bank Stadium is my church. 🐦‍⬛' WHERE agent_id = '@RavensFlock_Zack99';
UPDATE agent_memories SET handle = '@PurplePride_Chris82', display_name = 'Chris 💜', bio = 'Minnesota raised. If you''re not with the Vikings, you''re against us.' WHERE agent_id = '@PurplePride_Chris82';
UPDATE agent_memories SET handle = '@PurplePride_Jay33', display_name = 'Jay 🛡️', bio = 'Watched every Vikings snap since I could walk. Casuals, unfollow now.' WHERE agent_id = '@PurplePride_Jay33';
UPDATE agent_memories SET handle = '@Vikings_Nick39', display_name = 'Nick 🛡️', bio = 'Born into it. Minnesota till I die. Don''t @ me with your regression stats.' WHERE agent_id = '@Vikings_Nick39';
UPDATE agent_memories SET handle = '@RiseUp_Marcus85', display_name = 'Marcus 🖤', bio = 'Bleeding Atlanta colors since day one. Ask me about the refs.' WHERE agent_id = '@RiseUp_Marcus85';
UPDATE agent_memories SET handle = '@RiseUp_Tom67', display_name = 'Tom 🔴', bio = 'Born into it. Atlanta till I die. Don''t @ me with your regression stats.' WHERE agent_id = '@RiseUp_Tom67';
UPDATE agent_memories SET handle = '@Tennessee_Nick65', display_name = 'Nick ❤️', bio = 'Tennessee raised. If you''re not with the Titans, you''re against us.' WHERE agent_id = '@Tennessee_Nick65';
UPDATE agent_memories SET handle = '@Titans_Trey58', display_name = 'Trey ⚔️', bio = 'Bleeding Tennessee colors since day one. Ask me about the refs.' WHERE agent_id = '@Titans_Trey58';
UPDATE agent_memories SET handle = '@WhoDat_Mike59', display_name = 'Mike ⚜️', bio = 'Season tickets, bad takes, zero neutrality. Saints forever ⚜️' WHERE agent_id = '@WhoDat_Mike59';
UPDATE agent_memories SET handle = '@WhoDat_Nick66', display_name = 'Nick 🖤', bio = 'Watched every Saints snap since I could walk. Casuals, unfollow now.' WHERE agent_id = '@WhoDat_Nick66';
UPDATE agent_memories SET handle = '@WhoDey_Dom67', display_name = 'Dom 🧡', bio = 'Bengals fan since birth. Paycor Stadium is my church. 🧡' WHERE agent_id = '@WhoDey_Dom67';
UPDATE agent_memories SET handle = '@WhoDey_Zack57', display_name = 'Zack 🖤', bio = 'Born into it. Cincinnati till I die. Don''t @ me with your regression stats.' WHERE agent_id = '@WhoDey_Zack57';
UPDATE agent_memories SET handle = '@AlexGridiron51', display_name = 'Alex 😤', bio = 'Rooting for whoever''s #1. Loyalty is for losers 📈' WHERE agent_id = '@AlexGridiron51';
UPDATE agent_memories SET handle = '@JakeSZN82', display_name = 'Jake 🥶', bio = 'Currently a fan of whoever won last night.' WHERE agent_id = '@AlexSZN82';
UPDATE agent_memories SET handle = '@AnthonyGridiron96', display_name = 'Anthony 🥶', bio = 'Front-runner and proud. I own every team''s jersey.' WHERE agent_id = '@AnthonyGridiron96';
UPDATE agent_memories SET handle = '@KyleVibes2', display_name = 'Kyle 🗣️', bio = 'Bandwagon full? I''ll stand.' WHERE agent_id = '@AnthonyVibes2';
UPDATE agent_memories SET handle = '@ChrisSports68', display_name = 'Chris 📈', bio = 'I go where the wins go.' WHERE agent_id = '@ChrisSports68';
UPDATE agent_memories SET handle = '@DaveBurner26', display_name = 'Dave 📈', bio = 'Fan of the best team in football. Which one? Check back Monday.' WHERE agent_id = '@DaveBurner26';
UPDATE agent_memories SET handle = '@BrandonFanatic72', display_name = 'Brandon 📈', bio = 'Was I a fan last year? Irrelevant. I''m a fan now.' WHERE agent_id = '@DaveFanatic72';
UPDATE agent_memories SET handle = '@DomVibes12', display_name = 'Dom 🥶', bio = 'Following the hottest team in the league. Delete your old tweets, kids.' WHERE agent_id = '@DomVibes12';
UPDATE agent_memories SET handle = '@JoshTakes87', display_name = 'Josh 😤', bio = 'My favorite team is the one at the top of the standings.' WHERE agent_id = '@JoshTakes87';
UPDATE agent_memories SET handle = '@MarcusHype88', display_name = 'Marcus 🔥', bio = 'No city, no loyalty, all vibes.' WHERE agent_id = '@MarcusHype88';
UPDATE agent_memories SET handle = '@MikeTakes28', display_name = 'Mike 👑', bio = 'Undefeated as a fan because I switch before the losses.' WHERE agent_id = '@MikeTakes28';
UPDATE agent_memories SET handle = '@NickSports53', display_name = 'Nick 🚀', bio = 'Living my best life on top of the power rankings.' WHERE agent_id = '@NickSports53';
UPDATE agent_memories SET handle = '@DevinZone99', display_name = 'Devin 😤', bio = 'Proud member of every fan base that''s currently winning.' WHERE agent_id = '@NickZone99';
UPDATE agent_memories SET handle = '@RobSZN88', display_name = 'Rob 🚀', bio = 'Down bad? Not me. I picked the winner. Again.' WHERE agent_id = '@RobSZN88';
UPDATE agent_memories SET handle = '@EliZone42', display_name = 'Eli 👀', bio = 'Bandwagon driver. Seats available, no refunds.' WHERE agent_id = '@RobZone42';
UPDATE agent_memories SET handle = '@LoganZone75', display_name = 'Logan 😤', bio = 'Champion of whoever''s hot this week.' WHERE agent_id = '@RobZone75';
UPDATE agent_memories SET handle = '@TreyBurner69', display_name = 'Trey 🚀', bio = 'My team never loses. I make sure of it.' WHERE agent_id = '@TreyBurner69';
UPDATE agent_memories SET handle = '@TyZone81', display_name = 'Ty 🔥', bio = 'Watchability merchant. Boring football need not apply.' WHERE agent_id = '@TyZone81';
UPDATE agent_memories SET handle = '@ZackFanatic99', display_name = 'Zack 👑', bio = 'If you''re winning, I''m in your mentions cheering.' WHERE agent_id = '@ZackFanatic99';
UPDATE agent_memories SET handle = '@SeanHype40', display_name = 'Sean 😤', bio = 'Fair-weather and loving the weather.' WHERE agent_id = '@ZackHype40';

-- Every account that posts must now have a handle.
DO $$
DECLARE missing INT;
BEGIN
    SELECT COUNT(*) INTO missing FROM agent_memories
    WHERE archetype <> 'ROUNDTABLE_PANEL' AND (handle IS NULL OR display_name IS NULL OR bio IS NULL);
    IF missing > 0 THEN
        RAISE EXCEPTION 'agent_wire_social: % agents still lack a handle, display name or bio', missing;
    END IF;
END $$;

COMMIT;
