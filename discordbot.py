import os
import discord
from discord.ext import commands
import random

# Bot設定
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# フォーラムやスレッドのID（必要に応じて設定）
FORUM_CHANNEL_ID = 1288321432828248124
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1307321645480022046
FAVORITE_REACTION_ID = 1307735348184354846
RANDOM_EXCLUDE_REACTION_ID = 1304763661172346973
READ_LATER_INCLUDE_REACTION_ID = 1306461538659340308

# メッセージキャッシュを保持する辞書
message_cache = {}

async def update_message_cache(thread_id):
    """
    指定されたスレッドのメッセージをキャッシュに保存。
    """
    forum_channel = bot.get_channel(FORUM_CHANNEL_ID)
    if forum_channel is None:
        return
    thread = forum_channel.get_thread(thread_id)
    if thread:
        message_cache[thread_id] = [message async for message in thread.history(limit=100)]

async def get_cached_messages(thread_id):
    """
    キャッシュされたメッセージを取得。
    キャッシュがない場合は更新。
    """
    if thread_id not in message_cache:
        await update_message_cache(thread_id)
    return message_cache.get(thread_id, [])

def has_reaction(message, reaction_id):
    """
    指定されたリアクションがメッセージにあるか確認。
    """
    return any(
        reaction.emoji.id == reaction_id for reaction in message.reactions if hasattr(reaction.emoji, 'id')
    )

async def select_random_message(thread_id, user_id, filter_func=None):
    """
    スレッドからランダムなメッセージを選択。
    filter_funcが指定された場合、条件を適用。
    """
    messages = await get_cached_messages(thread_id)
    filtered_messages = [msg for msg in messages if msg.author.id != user_id]
    if filter_func:
        filtered_messages = [msg for msg in filtered_messages if filter_func(msg)]
    return random.choice(filtered_messages) if filtered_messages else None

class MangaSelectorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ランダム(通常)", style=discord.ButtonStyle.primary)
    async def recommend_manga(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(THREAD_ID, interaction.user.id)
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("おすすめの漫画が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="あとで読む(通常)", style=discord.ButtonStyle.primary)
    async def later_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(
            THREAD_ID, interaction.user.id,
            filter_func=lambda msg: has_reaction(msg, READ_LATER_REACTION_ID)
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(
            THREAD_ID, interaction.user.id,
            filter_func=lambda msg: has_reaction(msg, FAVORITE_REACTION_ID)
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger)
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(
            THREAD_ID, interaction.user.id,
            filter_func=lambda msg: not has_reaction(msg, RANDOM_EXCLUDE_REACTION_ID)
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger)
    async def read_later_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(
            THREAD_ID, interaction.user.id,
            filter_func=lambda msg: not has_reaction(msg, RANDOM_EXCLUDE_REACTION_ID) and has_reaction(msg, READ_LATER_INCLUDE_REACTION_ID)
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

@bot.tree.command(name="panel", description="エロ漫画ルーレットパネルを表示します")
async def panel(interaction: discord.Interaction):
    """
    パネルを表示するスラッシュコマンド。
    """
    embed = discord.Embed(
        title="🎯ｴﾛ漫画ﾙｰﾚｯﾄ",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "【ランダム】　：全体から選ぶ\n"
            "【あとで読む】：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "【お気に入り】：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=discord.Color.magenta()
    )
    view = MangaSelectorView()
    await interaction.response.send_message(embed=embed, view=view)

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    try:
        synced = await bot.tree.sync()  # 明示的にコマンド同期
        print(f"Commands synced successfully: {len(synced)} commands")
    except Exception as e:
        print(f"Error syncing commands: {e}")

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
