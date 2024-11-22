import os
import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import random

# DiscordとHerokuの設定
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True  # 必須
bot = commands.Bot(command_prefix="!", intents=intents)

DATABASE_URL = os.getenv('DATABASE_URL')
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# 各種ID設定
FORUM_CHANNEL_ID = 1288321432828248124
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1307321645480022046
FAVORITE_REACTION_ID = 1307735348184354846

# データベース接続
def initialize_database():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cursor = conn.cursor()
    # 必要ならテーブル作成などの初期化処理を追加
    print("Database initialized")
    return conn, cursor

conn, cursor = initialize_database()

# Embedの作成
def create_embed(result: str = None):
    description = (
        "botがおすすめの漫画を選んでくれるよ！\n\n"
        "🔵: 自分のリアクションを除外しない\n"
        "🔴: 自分のリアクションを除外する\n\n"
        "**ランダム(通常):** 全体から選ぶ\n"
        "**あとで読む:** 特定のリアクションから選ぶ\n"
        "**お気に入り:** お気に入りのリアクションから選ぶ\n"
    )
    if result:
        description += f"**結果:** {result}"
    return discord.Embed(
        title="おすすめ漫画セレクター",
        description=description,
        color=discord.Color.magenta()
    )

# ボタンの作成
def create_view():
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="ランダム(通常)", custom_id="recommend_manga", style=discord.ButtonStyle.primary))
    view.add_item(discord.ui.Button(label="あとで読む", custom_id="later_read", style=discord.ButtonStyle.primary))
    view.add_item(discord.ui.Button(label="お気に入り", custom_id="favorite", style=discord.ButtonStyle.primary))
    return view

# ボタン付きメッセージの送信
async def create_button_message(channel):
    embed = create_embed()
    view = create_view()
    await channel.send(embed=embed, view=view)

# ボタンのインタラクション処理
async def handle_interaction(interaction, action: str):
    try:
        forum_channel = bot.get_channel(FORUM_CHANNEL_ID)
        thread = forum_channel.get_thread(THREAD_ID)
        messages = [message async for message in thread.history(limit=100)]
        result = "条件に合うメッセージが見つかりませんでした。"

        # アクションに基づいた処理
        if action == "recommend_manga":
            if messages:
                random_message = random.choice(messages)
                result = f"おすすめの漫画はこちら: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
        elif action == "later_read":
            filtered = [
                msg for msg in messages if any(
                    reaction.emoji.id == READ_LATER_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
                )
            ]
            if filtered:
                random_message = random.choice(filtered)
                result = f"あとで読む: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
        elif action == "favorite":
            filtered = [
                msg for msg in messages if any(
                    reaction.emoji.id == FAVORITE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
                )
            ]
            if filtered:
                random_message = random.choice(filtered)
                result = f"お気に入り: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"

        # Embedを更新
        updated_embed = create_embed(result)
        await interaction.message.edit(embed=updated_embed, view=create_view())
        await interaction.response.defer()

    except Exception as e:
        print(f"インタラクション処理中にエラーが発生しました: {e}")
        await interaction.response.send_message("エラーが発生しました。再試行してください。", ephemeral=True)

# /add コマンド
@bot.tree.command(name="add", description="ボタン付きメッセージを表示します")
async def add(interaction: discord.Interaction):
    await interaction.response.defer()
    await create_button_message(interaction.channel)
    await interaction.followup.send("ボタンを作成しました！", ephemeral=True)

# Botの準備完了イベント
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        print("スラッシュコマンドが同期されました。")
    except Exception as e:
        print(f"スラッシュコマンドの同期中にエラーが発生しました: {e}")
    print(f"Logged in as {bot.user}")

# インタラクションイベント
@bot.event
async def on_interaction(interaction: discord.Interaction):
    try:
        action = interaction.data['custom_id']
        await handle_interaction(interaction, action)
    except Exception as e:
        print(f"インタラクション処理中にエラーが発生しました: {e}")

# Botの実行
if __name__ == "__main__":
    if DISCORD_TOKEN:
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"Botの起動中にエラーが発生しました: {e}")
    else:
        print("DISCORD_TOKENが設定されていません。")
