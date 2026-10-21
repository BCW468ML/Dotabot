import os
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

queue = []

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"{bot.user} is online!")

@bot.tree.command(name="queue", description="Join the Dota 2 inhouse queue")
async def join_queue(interaction: discord.Interaction):
    if interaction.user.id in queue:
        await interaction.response.send_message(
            "You're already in the queue!", ephemeral=True
        )
        return

    if len(queue) >= 10:
        await interaction.response.send_message(
            "The queue is full!", ephemeral=True
        )
        return

    queue.append(interaction.user.id)
    await interaction.response.send_message(
        f"{interaction.user.mention} joined the queue! ({len(queue)}/10)"
    )

    if len(queue) == 10:
        await interaction.channel.send(
            "⚔️ **INHOUSE READY!** 10 players have joined. Time to draft!"
        )

@bot.tree.command(name="leave", description="Leave the inhouse queue")
async def leave_queue(interaction: discord.Interaction):
    if interaction.user.id not in queue:
        await interaction.response.send_message(
            "You're not in the queue!", ephemeral=True
        )
        return

    queue.remove(interaction.user.id)
    await interaction.response.send_message(
        f"{interaction.user.mention} left the queue! ({len(queue)}/10)"
    )

@bot.tree.command(name="players", description="Show queued players")
async def show_players(interaction: discord.Interaction):
    if not queue:
        await interaction.response.send_message("The queue is empty!")
        return

    players = "\n".join(
        f"{i}. <@{user_id}>" for i, user_id in enumerate(queue, 1)
    )
    await interaction.response.send_message(
        f"**Dota 2 Inhouse Queue ({len(queue)}/10)**\n{players}"
    )

bot.run(TOKEN)
