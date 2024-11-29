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
REACTION_ID = 1304759949309509672

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

def has_reaction_from_user(message, reaction_id, user_id):
    """
    指定されたユーザーが指定されたリアクションを押しているか確認。
    """
    for reaction in message.reactions:
        if hasattr(reaction.emoji, 'id') and reaction.emoji.id == reaction_id:
            users = [user async for user in reaction.users()]
            if any(user.id == user_id for user in users):
                return True
    return False

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

    @discord.ui.button(label="あとで読む(通常)", style=discord.ButtonStyle.primary)
    async def later_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(
            THREAD_ID, interaction.user.id,
            filter_func=lambda msg: has_reaction_from_user(msg, REACTION_ID, interaction.user.id)
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(
            THREAD_ID, interaction.user.id,
            filter_func=lambda msg: has_reaction_from_user(msg, REACTION_ID, interaction.user.id)
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger)
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(
            THREAD_ID, interaction.user.id,
            filter_func=lambda msg: not has_reaction_from_user(msg, REACTION_ID, interaction.user.id)
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger)
    async def read_later_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = await select_random_message(
            THREAD_ID, interaction.user.id,
            filter_func=lambda msg: not has_reaction_from_user(msg, REACTION_ID, interaction.user.id) and has_reaction_from_user(msg, REACTION_ID, interaction.user.id)
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

@bot.command()
async def panel(ctx):
    """
    パネルを表示するコマンド。
    """
    embed = discord.Embed(
        title="🎯ｴﾛ漫画ﾙｰﾚｯﾄ",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分のリアクションを含む投稿\n"
            "🔴：自分のリアクションを含まない投稿\n\n"
            "【ランダム】　：全体から選ぶ\n"
            "【あとで読む】：特定のリアクションを付けた投稿から選ぶ\n"
            "【お気に入り】：お気に入りの投稿を選ぶ"
        ),
        color=discord.Color.magenta()
    )
    view = MangaSelectorView()
    await ctx.send(embed=embed, view=view)

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
