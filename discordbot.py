import os
import discord
from discord.ext import commands
from discord import app_commands
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

# コマンドを使用可能なロールID
ALLOWED_ROLE_IDS = [1283962068197965897, 1246804322969456772]

# 投稿を取得する関数
async def get_recommendation(action: str):
    """
    各アクションに応じて投稿を選びます。
    """
    forum_channel = bot.get_channel(FORUM_CHANNEL_ID)
    if forum_channel is None:
        return "フォーラムチャンネルが見つかりません。"

    thread = forum_channel.get_thread(THREAD_ID)
    if thread is None:
        return "スレッドが見つかりません。"

    messages = [message async for message in thread.history(limit=100)]
    if not messages:
        return "スレッド内に投稿が見つかりません。"

    if action == "recommend_manga":
        random_message = random.choice(messages)
        return f"おすすめの漫画はこちら: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "later_read":
        filtered = [
            msg for msg in messages if any(
                reaction.emoji.id == READ_LATER_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
        return f"あとで読む: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "favorite":
        filtered = [
            msg for msg in messages if any(
                reaction.emoji.id == FAVORITE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "お気に入りの投稿が見つかりません。"
        random_message = random.choice(filtered)
        return f"お気に入り: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "random_exclude":
        filtered = [
            msg for msg in messages if not any(
                reaction.emoji.id == RANDOM_EXCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
        return f"ランダム(除外): [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
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
        return f"あとで読む(除外): [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    else:
        return "不明なアクションです。"

# ボタンビュー作成
class MangaSelectorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        # ボタン追加
        self.add_item(discord.ui.Button(label="ランダム(通常)", style=discord.ButtonStyle.primary, custom_id="recommend_manga"))
        self.add_item(discord.ui.Button(label="あとで読む(通常)", style=discord.ButtonStyle.primary, custom_id="later_read"))
        self.add_item(discord.ui.Button(label="お気に入り", style=discord.ButtonStyle.primary, custom_id="favorite"))
        self.add_item(discord.ui.Button(label="ランダム", style=discord.ButtonStyle.danger, custom_id="random_exclude"))
        self.add_item(discord.ui.Button(label="あとで読む", style=discord.ButtonStyle.danger, custom_id="read_later_exclude"))

    def create_embed(self, result):
        """
        Embedを作成
        """
        return discord.Embed(
            title="おすすめ漫画セレクター",
            description=(
                "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
                "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
                "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
                "【ランダム】　：全体から選ぶ\n"
                "【あとで読む】：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
                "【お気に入り】：<:b435:1304690627723657267>を付けた投稿から選ぶ\n\n"
                f"**結果**: {result}"
            ),
            color=discord.Color.magenta()  # ピンク色
        )

# スラッシュコマンド `/パネル` 作成
@bot.tree.command(name="パネル", description="おすすめ漫画セレクターパネルを表示します")
async def panel(interaction: discord.Interaction):
    """
    パネルを表示するスラッシュコマンド。
    """
    # ユーザーのロールを確認
    if not any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("このコマンドを使用する権限がありません。", ephemeral=True)
        return

    # パネルを表示
    embed = discord.Embed(
        title="おすすめ漫画セレクター",
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

# Bot起動
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()  # スラッシュコマンドを同期
        print(f"スラッシュコマンドを {len(synced)} 個同期しました。")
    except Exception as e:
        print(f"コマンド同期中にエラー: {e}")

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
