import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import asyncio
import logging
import psycopg2
from psycopg2 import pool
from psycopg2.extras import DictCursor

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# DATABASE_URL 環境変数を取得
DATABASE_URL = os.getenv("DATABASE_URL")

# コネクションプールの初期化
try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1, maxconn=10, dsn=DATABASE_URL, sslmode='require'
    )
except psycopg2.Error as e:
    logging.error(f"データベース接続プールの初期化中にエラー: {e}")
    db_pool = None

# データベース接続を取得
def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise psycopg2.Error("データベース接続プールが初期化されていません。")
    except psycopg2.Error as e:
        logging.error(f"データベース接続中にエラー: {e}")
        return None

# データベース接続をリリース
def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except psycopg2.Error as e:
        logging.error(f"データベース接続のリリース中にエラー: {e}")

# テーブルの初期化
def initialize_db():
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL UNIQUE,
                thread_id BIGINT NOT NULL,
                author_id BIGINT NOT NULL,
                reactions JSONB,
                content TEXT
            )
            """)
            conn.commit()
        logging.info("データベースの初期化が完了しました。")
    except psycopg2.Error as e:
        logging.error(f"テーブルの初期化中にエラー: {e}")
    finally:
        release_db_connection(conn)

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
RANDOM_EXCLUDE_REACTION_ID = 1310824310348316753

# メッセージをデータベースに保存
async def save_all_messages_to_db():
    conn = get_db_connection()
    if not conn:
        return
    try:
        channel = bot.get_channel(THREAD_ID)
        thread = channel
        async for message in thread.history(limit=None):
            reactions_dict = {
                str(reaction.emoji.id): [user.id async for user in reaction.users()]
                for reaction in message.reactions if hasattr(reaction.emoji, 'id')
            }
            with conn.cursor() as cur:
                cur.execute("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE SET reactions = EXCLUDED.reactions
                """, (
                    message.id,
                    THREAD_ID,
                    message.author.id,
                    str(reactions_dict),
                    message.content
                ))
                conn.commit()
    except psycopg2.Error as e:
        logging.error(f"メッセージ保存中にエラー: {e}")
    finally:
        release_db_connection(conn)

# タスクで定期的にメッセージ保存
@tasks.loop(minutes=10)
async def periodic_save_messages():
    await save_all_messages_to_db()

# ボタンのUI定義
class MangaSelectorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0)
    async def random_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, lambda msg: True)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0)
    async def read_later_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, lambda msg: str(READ_LATER_REACTION_ID) in msg['reactions'])

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0)
    async def favorite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, lambda msg: str(FAVORITE_REACTION_ID) in msg['reactions'])

    @discord.ui.button(label="ランダム除外", style=discord.ButtonStyle.danger, row=1)
    async def random_exclude_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, lambda msg: str(RANDOM_EXCLUDE_REACTION_ID) not in msg['reactions'])

    @discord.ui.button(label="条件付き読む", style=discord.ButtonStyle.danger, row=1)
    async def conditional_read_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(
            interaction,
            lambda msg: str(READ_LATER_REACTION_ID) in msg['reactions'] and
                        str(RANDOM_EXCLUDE_REACTION_ID) not in msg['reactions']
        )

    async def handle_selection(self, interaction, filter_func):
        try:
            random_message = get_random_message(THREAD_ID, filter_func)
            await interaction.response.edit_message(
                content=(
                    f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                    f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
                ),
                embed=None
            )
        except ValueError:
            await interaction.response.edit_message(content="条件に合う投稿が見つかりませんでした。", embed=None)
        except RuntimeError as e:
            await interaction.response.edit_message(content="データベースエラーが発生しました。", embed=None)

# コマンド定義
@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
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

# Bot起動時の処理
@bot.event
async def on_ready():
    periodic_save_messages.start()
    logging.info(f"Botが起動しました！ {bot.user}")

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    logging.error("DISCORD_TOKENが設定されていません。")
