import asyncio
import itertools
import logging
import math
import os
import random
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['DISCORD_TOKEN']
DB_PATH = Path(os.getenv('DB_PATH', '/data/dotabot.sqlite3'))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.execute('PRAGMA journal_mode=WAL')
conn.executescript('''
CREATE TABLE IF NOT EXISTS players (
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, mmr INTEGER NOT NULL DEFAULT 1000,
 wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS matches (
 guild_id INTEGER NOT NULL, match_id INTEGER PRIMARY KEY AUTOINCREMENT,
 mode TEXT NOT NULL, radiant TEXT NOT NULL, dire TEXT NOT NULL,
 radiant_captain INTEGER NOT NULL, dire_captain INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', reported_winner TEXT,
 reporter_id INTEGER, winner TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT);
''')

@dataclass
class Challenge:
    challenger: int
    challenged: int
    channel_id: int
    status: str = 'invited'
    pool: list[int] = field(default_factory=list)
    radiant: list[int] = field(default_factory=list)
    dire: list[int] = field(default_factory=list)
    turn: int = 0
    message_id: int | None = None

ranked_queues: dict[int, list[int]] = {}
challenges: dict[int, Challenge] = {}
locks: dict[int, asyncio.Lock] = {}


def guild_lock(guild_id):
    return locks.setdefault(guild_id, asyncio.Lock())


def get_player(guild_id, user_id):
    conn.execute('INSERT OR IGNORE INTO players(guild_id,user_id) VALUES (?,?)', (guild_id,user_id))
    return conn.execute('SELECT * FROM players WHERE guild_id=? AND user_id=?', (guild_id,user_id)).fetchone()


def mmr(guild_id, user_id):
    return get_player(guild_id,user_id)['mmr']


def balance(guild_id, players):
    # Fix the first player on Radiant to avoid evaluating mirrored splits.
    ratings = {p: mmr(guild_id,p) for p in players}
    best = None
    best_gap = float('inf')
    for rest in itertools.combinations(players[1:], 4):
        radiant = [players[0], *rest]
        rs = set(radiant)
        dire = [p for p in players if p not in rs]
        gap = abs(sum(ratings[p] for p in radiant) - sum(ratings[p] for p in dire))
        if gap < best_gap:
            best_gap, best = gap, (radiant,dire)
    return best


def make_match(guild_id, mode, radiant, dire, rc, dc):
    import json
    cursor = conn.execute('''INSERT INTO matches
      (guild_id,mode,radiant,dire,radiant_captain,dire_captain)
      VALUES (?,?,?,?,?,?)''', (guild_id,mode,json.dumps(radiant),json.dumps(dire),rc,dc))
    return cursor.lastrowid


def format_team(guild_id, members):
    return '\n'.join(f'<@{p}> — {mmr(guild_id,p)} MMR' for p in members)


def match_announcement(guild_id, match_id, mode, radiant, dire):
    r_total = sum(mmr(guild_id,p) for p in radiant)
    d_total = sum(mmr(guild_id,p) for p in dire)
    return (f'⚔️ **Match #{match_id} — {mode.title()}**\n'
            f'**Radiant ({r_total} total MMR)**\n{format_team(guild_id,radiant)}\n\n'
            f'**Dire ({d_total} total MMR)**\n{format_team(guild_id,dire)}\n\n'
            f'Captains: <@{radiant[0]}> and <@{dire[0]}>. '
            'After playing, one captain uses `/report` and the other uses `/confirm`.')


def player_busy(guild_id, user_id):
    c = challenges.get(guild_id)
    return bool(c and c.status in ('invited','pool','draft') and
                (user_id in (c.challenger,c.challenged) or user_id in c.pool))


class DraftSelect(discord.ui.Select):
    def __init__(self, guild_id, challenge):
        options = [discord.SelectOption(label=f'{i+1}. {mmr(guild_id,p)} MMR',
                                        value=str(p), description=f'Player ID: {p}')
                   for i,p in enumerate(challenge.pool[:25])]
        super().__init__(placeholder='Choose a player to draft', min_values=1,
                         max_values=1, options=options)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        gid = self.guild_id
        async with guild_lock(gid):
            c = challenges.get(gid)
            if not c or c.status != 'draft':
                await interaction.response.send_message('This draft is no longer active.', ephemeral=True)
                return
            current = c.challenger if c.turn % 2 == 0 else c.challenged
            if interaction.user.id != current:
                await interaction.response.send_message('It is not your turn to draft.', ephemeral=True)
                return
            selected = int(self.values[0])
            if selected not in c.pool:
                await interaction.response.send_message('That player is no longer available.', ephemeral=True)
                return
            c.pool.remove(selected)
            (c.radiant if c.turn % 2 == 0 else c.dire).append(selected)
            c.turn += 1
            await interaction.response.defer()
            if c.turn == 8:
                match_id = make_match(gid,'challenge',c.radiant,c.dire,c.challenger,c.challenged)
                c.status = 'complete'
                await interaction.message.edit(content='Draft complete!', view=None)
                await interaction.followup.send(match_announcement(gid,match_id,'challenge',c.radiant,c.dire))
                challenges.pop(gid,None)
            else:
                next_captain = c.challenger if c.turn % 2 == 0 else c.challenged
                await interaction.message.edit(
                    content=f'**Challenge Draft** — Pick {c.turn+1}/8. <@{next_captain}> is picking.\n'
                            f'Radiant: {len(c.radiant)}/5 | Dire: {len(c.dire)}/5',
                    view=DraftView(gid,c))


class DraftView(discord.ui.View):
    def __init__(self, guild_id, challenge):
        super().__init__(timeout=None)
        self.add_item(DraftSelect(guild_id,challenge))


class Dotabot(commands.Bot):
    async def setup_hook(self):
        print("Starting slash command sync...", flush=True)
        synced = await self.tree.sync()
        print(
            f"SYNC COMPLETE: {len(synced)} commands: "
            f"{[cmd.name for cmd in synced]}",
            flush=True
        )





bot = Dotabot(command_prefix='!', intents=discord.Intents.default())

@bot.event
async def on_ready():
    logging.info('Logged in as %s',bot.user)


def guild_only(interaction):
    return interaction.guild_id is not None


@bot.tree.command(name='queue',description='Join the 10-player ranked inhouse queue')
@app_commands.guild_only()
async def queue(interaction: discord.Interaction):
    gid = interaction.guild_id
    async with guild_lock(gid):
        q = ranked_queues.setdefault(gid,[])
        uid = interaction.user.id
        if uid in q:
            msg = 'You are already in the ranked queue.'
        elif player_busy(gid,uid):
            msg = 'Leave the active challenge first.'
        elif len(q) >= 10:
            msg = 'The ranked queue is full.'
        else:
            get_player(gid,uid)
            q.append(uid)
            msg = f'{interaction.user.mention} joined ranked! ({len(q)}/10)'
        await interaction.response.send_message(msg)
        if len(q) == 10 and uid in q:
            players = q[:]
            q.clear()
            radiant,dire = balance(gid,players)
            match_id = make_match(gid,'ranked',radiant,dire,radiant[0],dire[0])
            await interaction.followup.send(match_announcement(gid,match_id,'ranked',radiant,dire))


@bot.tree.command(name='leave',description='Leave the ranked inhouse queue')
@app_commands.guild_only()
async def leave(interaction: discord.Interaction):
    gid = interaction.guild_id
    async with guild_lock(gid):
        q = ranked_queues.setdefault(gid,[])
        if interaction.user.id not in q:
            await interaction.response.send_message('You are not in the ranked queue.',ephemeral=True)
        else:
            q.remove(interaction.user.id)
            await interaction.response.send_message(f'You left ranked. ({len(q)}/10)')


@bot.tree.command(name='players',description='Show players in the ranked queue')
@app_commands.guild_only()
async def players(interaction: discord.Interaction):
    q = ranked_queues.get(interaction.guild_id,[])
    msg = '\n'.join(f'{i}. <@{p}> — {mmr(interaction.guild_id,p)} MMR' for i,p in enumerate(q,1))
    await interaction.response.send_message(f'**Ranked queue ({len(q)}/10)**\n{msg or "Empty"}')


challenge_group = app_commands.Group(name='challenge',description='Challenge mode with captain drafting',guild_only=True)

@challenge_group.command(name='start',description='Challenge another player to captain a match')
async def challenge_start(interaction: discord.Interaction, opponent: discord.Member):
    gid = interaction.guild_id
    async with guild_lock(gid):
        if opponent.bot or opponent.id == interaction.user.id:
            msg = 'Choose another human player.'
        elif gid in challenges:
            msg = 'There is already an active challenge in this server.'
        elif interaction.user.id in ranked_queues.get(gid,[]) or opponent.id in ranked_queues.get(gid,[]):
            msg = 'Both captains must leave the ranked queue first.'
        else:
            get_player(gid,interaction.user.id)
            get_player(gid,opponent.id)
            challenges[gid] = Challenge(interaction.user.id,opponent.id,interaction.channel_id)
            msg = f'⚔️ {opponent.mention}, <@{interaction.user.id}> challenges you! Use `/challenge accept` or `/challenge decline`.'
        await interaction.response.send_message(msg)

@challenge_group.command(name='accept',description='Accept a challenge against you')
async def challenge_accept(interaction: discord.Interaction):
    gid = interaction.guild_id
    async with guild_lock(gid):
        c = challenges.get(gid)
        if not c or c.status != 'invited' or interaction.user.id != c.challenged:
            msg = 'You have no pending challenge to accept.'
        else:
            c.status = 'pool'
            msg = 'Challenge accepted! Up to **25 volunteers** may use `/challenge join`. Captains use `/challenge draft` when at least 8 volunteers have joined.'
        await interaction.response.send_message(msg)

@challenge_group.command(name='decline',description='Decline a challenge against you')
async def challenge_decline(interaction: discord.Interaction):
    gid = interaction.guild_id
    async with guild_lock(gid):
        c = challenges.get(gid)
        if not c or c.status != 'invited' or interaction.user.id != c.challenged:
            msg = 'No challenge to decline.'
        else:
            challenges.pop(gid,None)
            msg = 'Challenge declined.'
        await interaction.response.send_message(msg)

@challenge_group.command(name='join',description='Join the challenge volunteer pool')
async def challenge_join(interaction: discord.Interaction):
    gid = interaction.guild_id
    async with guild_lock(gid):
        c = challenges.get(gid)
        uid = interaction.user.id
        if not c or c.status != 'pool':
            msg = 'No challenge pool is open.'
        elif uid in (c.challenger,c.challenged):
            msg = 'Captains are already included.'
        elif uid in ranked_queues.get(gid,[]):
            msg = 'Leave the ranked queue first.'
        elif uid in c.pool:
            msg = 'You are already in the pool.'
        elif len(c.pool) >= 25:
            msg = 'The 25-player volunteer pool is full.'
        else:
            get_player(gid,uid)
            c.pool.append(uid)
            msg = f'{interaction.user.mention} joined the challenge pool ({len(c.pool)}/25).'
        await interaction.response.send_message(msg)

@challenge_group.command(name='leave',description='Leave the challenge volunteer pool')
async def challenge_leave(interaction: discord.Interaction):
    gid = interaction.guild_id
    async with guild_lock(gid):
        c = challenges.get(gid)
        if not c or c.status != 'pool' or interaction.user.id not in c.pool:
            msg = 'You are not in an open challenge pool.'
        else:
            c.pool.remove(interaction.user.id)
            msg = f'You left the challenge pool ({len(c.pool)}/25).'
        await interaction.response.send_message(msg,ephemeral=True)

@challenge_group.command(name='status',description='Show challenge status and volunteer pool')
async def challenge_status(interaction: discord.Interaction):
    c = challenges.get(interaction.guild_id)
    if not c:
        msg = 'No active challenge.'
    else:
        msg = (f'**Challenge: <@{c.challenger}> vs <@{c.challenged}>**\n'
               f'Status: {c.status} | Volunteers: {len(c.pool)}/25\n'
               + '\n'.join(f'<@{p}> — {mmr(interaction.guild_id,p)} MMR' for p in c.pool))
    await interaction.response.send_message(msg)

@challenge_group.command(name='draft',description='Start captain drafting with at least 8 volunteers')
async def challenge_draft(interaction: discord.Interaction):
    gid = interaction.guild_id
    async with guild_lock(gid):
        c = challenges.get(gid)
        if not c or c.status != 'pool':
            await interaction.response.send_message('No open challenge pool.',ephemeral=True)
            return
        if interaction.user.id not in (c.challenger,c.challenged):
            await interaction.response.send_message('Only captains can start the draft.',ephemeral=True)
            return
        if len(c.pool) < 8:
            await interaction.response.send_message('At least 8 volunteers are needed.',ephemeral=True)
            return
        c.status = 'draft'
        c.radiant = [c.challenger]
        c.dire = [c.challenged]
        c.turn = 0
        await interaction.response.send_message(
            f'**Challenge Draft** — Pick 1/8. <@{c.challenger}> is picking.\nRadiant: 1/5 | Dire: 1/5',
            view=DraftView(gid,c))

@challenge_group.command(name='cancel',description='Cancel a challenge (captains or Manage Server)')
async def challenge_cancel(interaction: discord.Interaction):
    gid = interaction.guild_id
    async with guild_lock(gid):
        c = challenges.get(gid)
        if not c:
            msg = 'No active challenge.'
        elif interaction.user.id not in (c.challenger,c.challenged) and not interaction.user.guild_permissions.manage_guild:
            msg = 'Only captains or server managers can cancel.'
        else:
            challenges.pop(gid,None)
            msg = 'Challenge canceled.'
        await interaction.response.send_message(msg)

bot.tree.add_command(challenge_group)


@bot.tree.command(name='profile',description='Show your inhouse rating and record')
@app_commands.guild_only()
async def profile(interaction: discord.Interaction, player: discord.Member | None = None):
    target = player or interaction.user
    p = get_player(interaction.guild_id,target.id)
    await interaction.response.send_message(
        f'**{target.display_name}** — {p["mmr"]} MMR | {p["wins"]}W / {p["losses"]}L')

@bot.tree.command(name='leaderboard',description='Top 10 inhouse players by shared MMR')
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction):
    rows = conn.execute('SELECT * FROM players WHERE guild_id=? ORDER BY mmr DESC,wins DESC LIMIT 10',
                        (interaction.guild_id,)).fetchall()
    msg = '\n'.join(f'{i}. <@{p["user_id"]}> — {p["mmr"]} MMR ({p["wins"]}W/{p["losses"]}L)'
                    for i,p in enumerate(rows,1))
    await interaction.response.send_message('🏆 **Inhouse leaderboard**\n'+(msg or 'No players yet.'))

@bot.tree.command(name='report',description='Captain: report the winning team for a match')
@app_commands.guild_only()
@app_commands.choices(winner=[app_commands.Choice(name='Radiant',value='radiant'),
                              app_commands.Choice(name='Dire',value='dire')])
async def report(interaction: discord.Interaction, match_id: int, winner: app_commands.Choice[str]):
    gid = interaction.guild_id
    async with guild_lock(gid):
        m = conn.execute('SELECT * FROM matches WHERE guild_id=? AND match_id=?',(gid,match_id)).fetchone()
        if not m or m['status'] != 'pending':
            msg = 'Match not found or already reported.'
        elif interaction.user.id not in (m['radiant_captain'],m['dire_captain']):
            msg = 'Only a match captain can report its result.'
        else:
            conn.execute('UPDATE matches SET status=?,reported_winner=?,reporter_id=? WHERE match_id=?',
                         ('awaiting_confirmation',winner.value,interaction.user.id,match_id))
            other = m['dire_captain'] if interaction.user.id == m['radiant_captain'] else m['radiant_captain']
            msg = f'Match #{match_id}: **{winner.value.title()}** reported as winner. <@{other}>, use `/confirm match_id:{match_id}`.'
        await interaction.response.send_message(msg)

@bot.tree.command(name='confirm',description='Other captain: confirm or dispute a reported match result')
@app_commands.guild_only()
async def confirm(interaction: discord.Interaction, match_id: int, agree: bool = True):
    import json
    gid = interaction.guild_id
    async with guild_lock(gid):
        m = conn.execute('SELECT * FROM matches WHERE guild_id=? AND match_id=?',(gid,match_id)).fetchone()
        if not m or m['status'] != 'awaiting_confirmation':
            msg = 'No result is awaiting confirmation for that match.'
        elif interaction.user.id not in (m['radiant_captain'],m['dire_captain']) or interaction.user.id == m['reporter_id']:
            msg = 'Only the other captain can confirm or dispute this result.'
        elif not agree:
            conn.execute('UPDATE matches SET status=? WHERE match_id=?',('disputed',match_id))
            msg = f'Match #{match_id} disputed. An admin must resolve it manually.'
        else:
            radiant = json.loads(m['radiant'])
            dire = json.loads(m['dire'])
            r_avg = sum(mmr(gid,p) for p in radiant)/5
            d_avg = sum(mmr(gid,p) for p in dire)/5
            expected_r = 1/(1+10**((d_avg-r_avg)/400))
            actual_r = 1 if m['reported_winner']=='radiant' else 0
            delta = round(32*(actual_r-expected_r))
            # Same rating delta per player, calculated before any changes.
            with conn:
                for uid in radiant:
                    conn.execute('UPDATE players SET mmr=MAX(0,mmr+?),wins=wins+?,losses=losses+? WHERE guild_id=? AND user_id=?',
                                 (delta,actual_r,1-actual_r,gid,uid))
                for uid in dire:
                    conn.execute('UPDATE players SET mmr=MAX(0,mmr-?),wins=wins+?,losses=losses+? WHERE guild_id=? AND user_id=?',
                                 (delta,1-actual_r,actual_r,gid,uid))
                conn.execute('UPDATE matches SET status=?,winner=?,finished_at=CURRENT_TIMESTAMP WHERE match_id=?',
                             ('completed',m['reported_winner'],match_id))
            msg = f'🏆 Match #{match_id} confirmed! **{m["reported_winner"].title()}** wins. Radiant MMR {delta:+d} per player; Dire MMR {-delta:+d} per player.'
        await interaction.response.send_message(msg)

@bot.tree.command(name='resolve',description='Admin: resolve a disputed match without rating changes')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def resolve(interaction: discord.Interaction, match_id: int):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message('Manage Server permission required.',ephemeral=True)
        return
    async with guild_lock(interaction.guild_id):
        m = conn.execute('SELECT status FROM matches WHERE guild_id=? AND match_id=?',
                         (interaction.guild_id,match_id)).fetchone()
        if not m or m['status'] != 'disputed':
            msg = 'That match is not disputed.'
        else:
            conn.execute('UPDATE matches SET status=? WHERE match_id=?',('void',match_id))
            msg = f'Match #{match_id} voided without rating changes.'
        await interaction.response.send_message(msg,ephemeral=True)

bot.run(TOKEN)
