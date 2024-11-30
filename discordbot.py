import os
import discord
from discord.ext import commands
from discord import app_commands
import random
import psycopg2
from psycopg2.extras import DictCursor

# DATABASE_URL 環境変数を取得
DATABASE_URL = os.getenv("DATABASE_URL")

# データベース接続
def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    except psycopg2.Error as e:
        raise Exception(f"Database connection error: {str(e)}")

# テーブルの初期化
def initialize_db():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    message_id BIGINT NOT NULL UNIQUE,
                    thread_id BIGINT NOT NULL,
                    author_id BIGINT NOT NULL,
                    reactions JSONB DEFAULT '{}'::JSONB,
                    content TEXT
                )
            """)
            conn.commit()

initialize_db()

# Bot設定
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# スレッドとリアクションIDの定義
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376
FAVORITE_REACTION_ID = 1304690627723657267
RANDOM_EXCLUDE_REACTION_ID = 1304763661172346973

# メッセージをデータベースに保存
async def save_message_to_db(message):
    try:
        reactions = {str(reaction.emoji.id): reaction.count for reaction in message.reactions if hasattr(reaction.emoji, 'id')}
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO UPDATE SET
                    reactions = EXCLUDED.reactions,
                    content = EXCLUDED.content
                """, (
                    message.id,
                    THREAD_ID,
                    message.author.id,
                    str(reactions),
                    message.content
                ))
                conn.commit()
    except Exception as e:
        print(f"Failed to save message {message.id} to database: {str(e)}")

# スレッド内のすべてのメッセージを保存
async def save_all_messages_to_db(thread_id):
    try:
        thread = bot.get_channel(thread_id)
        if thread is None:
            print(f"Thread with ID {thread_id} not found")
            return
        async for message in thread.history(limit=None):
            await save_message_to_db(message)
    except Exception as e:
        print(f"Error saving messages for thread {thread_id}: {str(e)}")

# メッセージをランダムに取得
def get_random_message(thread_id, filter_func=None):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
                messages = cur.fetchall()

                if filter_func:
                    messages = [msg for msg in messages if filter_func(msg)]

                if not messages:
                    return "エッチだなっつ！"  # メッセージが見つからない場合のデフォルトメッセージ

                return random.choice(messages)
    except psycopg2.Error as e:
        return f"Database error: {str(e)}"

# 新しいメッセージを保存
@bot.event
async def on_message(message):
    if message.channel.id == THREAD_ID:
        await save_message_to_db(message)

# リアクションが追加されたときに更新
@bot.event
async def on_raw_reaction_add(payload):
    try:
        if payload.channel_id == THREAD_ID:
            thread = bot.get_channel(payload.channel_id)
            if thread:
                message = await thread.fetch_message(payload.message_id)
                await save_message_to_db(message)
    except Exception as e:
        print(f"Error updating reactions for message {payload.message_id}: {str(e)}")

# ボタンのUI定義
class MangaSelectorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def handle_button_interaction(self, interaction, filter_func):
        try:
            random_message = get_random_message(THREAD_ID, filter_func)
            if isinstance(random_message, str):  # エラーメッセージの場合
                await interaction.response.send_message(random_message, ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                    f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
                )
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {str(e)}", ephemeral=True)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary)
    async def random_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_button_interaction(
            interaction,
            lambda msg: msg['author_id'] != interaction.user.id
        )

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.success)
    async def read_later_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_button_interaction(
            interaction,
            lambda msg: msg['author_id'] != interaction.user.id and str(READ_LATER_REACTION_ID) in msg['reactions']
        )

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.secondary)
    async def favorite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_button_interaction(
            interaction,
            lambda msg: msg['author_id'] != interaction.user.id and str(FAVORITE_REACTION_ID) in msg['reactions']
        )

    @discord.ui.button(label="ランダム除外", style=discord.ButtonStyle.danger)
    async def random_exclude_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_button_interaction(
            interaction,
            lambda msg: msg['author_id'] != interaction.user.id and str(RANDOM_EXCLUDE_REACTION_ID) not in msg['reactions']
        )

# コマンド定義
@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎯ｴﾛ漫画ﾙｰﾚｯﾄ",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "【ランダム】：全体から選ぶ\n"
            "【あとで読む】：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "【お気に入り】：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=discord.Color.magenta()
    )
    view = MangaSelectorView()
    await interaction.response.send_message(embed=embed, view=view)

# Bot起動時にメッセージキャッシュをデータベースに保存
@bot.event
async def on_ready():
    await save_all_messages_to_db(THREAD_ID)
    print(f"Botが起動しました！ {bot.user}")

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
