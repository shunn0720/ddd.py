import os
import discord
from discord.ext import commands
from discord import ui
import random

# Bot設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# フォーラムやスレッドのID（必要に応じて設定）
FORUM_CHANNEL_ID = 1288321432828248124
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1307321645480022046
FAVORITE_REACTION_ID = 1307735348184354846
RANDOM_EXCLUDE_REACTION_ID = 1304763661172346973
READ_LATER_INCLUDE_REACTION_ID = 1306461538659340308

# 投稿を取得する関数
async def get_recommendation(action: str, user_id: int):
    """
    各アクションに応じて投稿を選びます。
    """
    forum_channel = bot.get_channel(FORUM_CHANNEL_ID)
    if forum_channel is None:
        return "フォーラムチャンネルが見つかりません。"

    thread = forum_channel.get_thread(THREAD_ID)
    if thread is None:
        return "スレッドが見つかりません。"

    messages = [message async for message in thread.history(limit=100) if message.author.id != user_id]
    if not messages:
        return "スレッド内に投稿が見つかりません。"

    # 各アクションに応じたフィルタリング
    if action == "recommend_manga":
        random_message = random.choice(messages)
    elif action == "later_read":
        filtered = [
            msg for msg in messages if any(
                reaction.emoji.id == READ_LATER_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
    elif action == "favorite":
        filtered = [
            msg for msg in messages if any(
                reaction.emoji.id == FAVORITE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
    elif action == "random_exclude":
        filtered = [
            msg for msg in messages if not any(
                reaction.emoji.id == RANDOM_EXCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
    elif action == "read_later_exclude":
        filtered = [
            msg for msg in messages if not any(
                reaction.emoji.id == RANDOM_EXCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            ) and any(
                reaction.emoji.id == READ_LATER_INCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
    else:
        return "不明なアクションです。"

    message_link = f"https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id}"
    return f"{random_message.author.display_name} さんが投稿したこの本がおすすめだよ！\n{message_link}"

# ボタンビュー
class MangaSelectorView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        # ボタン追加
        self.add_item(ui.Button(label="ランダム(通常)", style=discord.ButtonStyle.primary, custom_id="recommend_manga"))
        self.add_item(ui.Button(label="あとで読む(通常)", style=discord.ButtonStyle.primary, custom_id="later_read"))
        self.add_item(ui.Button(label="お気に入り", style=discord.ButtonStyle.primary, custom_id="favorite"))
        self.add_item(ui.Button(label="ランダム", style=discord.ButtonStyle.danger, custom_id="random_exclude"))
        self.add_item(ui.Button(label="あとで読む", style=discord.ButtonStyle.danger, custom_id="read_later_exclude"))

    @ui.button(label="ランダム(通常)", style=discord.ButtonStyle.primary)
    async def recommend_manga(self, interaction: discord.Interaction, button: ui.Button):
        result = await get_recommendation("recommend_manga", interaction.user.id)
        await interaction.response.send_message(f"{interaction.user.mention} {result}")

    @ui.button(label="あとで読む(通常)", style=discord.ButtonStyle.primary)
    async def later_read(self, interaction: discord.Interaction, button: ui.Button):
        result = await get_recommendation("later_read", interaction.user.id)
        await interaction.response.send_message(f"{interaction.user.mention} {result}")

    @ui.button(label="お気に入り", style=discord.ButtonStyle.primary)
    async def favorite(self, interaction: discord.Interaction, button: ui.Button):
        result = await get_recommendation("favorite", interaction.user.id)
        await interaction.response.send_message(f"{interaction.user.mention} {result}")

    @ui.button(label="ランダム", style=discord.ButtonStyle.danger)
    async def random_exclude(self, interaction: discord.Interaction, button: ui.Button):
        result = await get_recommendation("random_exclude", interaction.user.id)
        await interaction.response.send_message(f"{interaction.user.mention} {result}")

    @ui.button(label="あとで読む", style=discord.ButtonStyle.danger)
    async def read_later_exclude(self, interaction: discord.Interaction, button: ui.Button):
        result = await get_recommendation("read_later_exclude", interaction.user.id)
        await interaction.response.send_message(f"{interaction.user.mention} {result}")

# スラッシュコマンドでEmbedを表示
@bot.tree.command(name="パネル")
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
        color=discord.Color.magenta()  # ピンク色
    )
    view = MangaSelectorView()
    await interaction.response.send_message(embed=embed, view=view)

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
