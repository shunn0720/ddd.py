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
from dotenv import load_dotenv
# 環境変数の読み込み
load_dotenv()

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
RANDOM_EXCLUDE_REACTION_ID = 1304763661172346973

# 非同期でリアクションの辞書を取得する関数
async def get_reactions_dict(message):
    reactions = {}
    for reaction in message.reactions:
        if hasattr(reaction.emoji, 'id'):  # カスタム絵文字の場合
            users = [user.id async for user in reaction.users()]
            reactions[str(reaction.emoji.id)] = users
    return reactions

# メッセージをデータベースに保存
async def save_message_to_db(message):
    conn = get_db_connection()
    if not conn:
        return
    try:
        reactions_dict = await get_reactions_dict(message)
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

# メッセージをランダムに取得
def get_random_message(thread_id, filter_func=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()
            if filter_func:
                messages = [msg for msg in messages if filter_func(msg)]
            if not messages:
                raise ValueError("指定された条件に合うメッセージがありませんでした。")
            return random.choice(messages)
    except psycopg2.Error as e:
        logging.error(f"データベース操作中にエラー: {e}")
        raise RuntimeError(f"データベースエラーが発生しました: {e}")
    except ValueError as e:
        logging.error(str(e))
        raise
    finally:
        release_db_connection(conn)

# ボタンのUI定義
class MangaSelectorViewBlue(discord.ui.View):
    """青ボタン用のView"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary)
    async def random_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            random_message = get_random_message(
                THREAD_ID,
                lambda msg: msg['author_id'] != interaction.user.id
            )
            await self.send_random_message(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary)
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            random_message = get_random_message(
                THREAD_ID,
                lambda msg: str(READ_LATER_REACTION_ID) in msg['reactions']
                and msg['author_id'] != interaction.user.id
            )
            await self.send_random_message(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            random_message = get_random_message(
                THREAD_ID,
                lambda msg: str(FAVORITE_REACTION_ID) in msg['reactions']
                and msg['author_id'] != interaction.user.id
            )
            await self.send_random_message(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    async def send_random_message(self, interaction, random_message):
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)


class MangaSelectorViewRed(discord.ui.View):
    """赤ボタン用のView"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ランダム除外", style=discord.ButtonStyle.danger)
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            random_message = get_random_message(
                THREAD_ID,
                lambda msg: str(RANDOM_EXCLUDE_REACTION_ID) not in msg['reactions']
                and msg['author_id'] != interaction.user.id
            )
            await self.send_random_message(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    @discord.ui.button(label="条件付き読む", style=discord.ButtonStyle.danger)
    async def conditional_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            random_message = get_random_message(
                THREAD_ID,
                lambda msg: str(FAVORITE_REACTION_ID) in msg['reactions']
                and str(RANDOM_EXCLUDE_REACTION_ID) not in msg['reactions']
                and msg['author_id'] != interaction.user.id
            )
            await self.send_random_message(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    async def send_random_message(self, interaction, random_message):
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)


# /panel コマンド
@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎯エロ漫画ルーレット",
        description=(
            "botがエロ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "【ランダム】：全体から選ぶ\n"
            "【あとで読む】：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "【お気に入り】：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=discord.Color.magenta()
    )
    view_blue = MangaSelectorViewBlue()
    view_red = MangaSelectorViewRed()
    await interaction.response.send_message(embed=embed, view=view_blue)
    await interaction.channel.send(view=view_red)  # 赤ボタンを下に表示


# Bot起動時の処理
@tasks.loop(minutes=60)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

@bot.event
async def on_ready():
    save_all_messages_to_db_task.start()
    logging.info(f"Botが起動しました！ {bot.user}")

@bot.event
async def on_shutdown():
    if save_all_messages_to_db_task.is_running():
        save_all_messages_to_db_task.cancel()
        logging.info("バックグラウンドタスクを停止しました。")
    if db_pool:
        db_pool.closeall()
        logging.info("データベース接続プールをクローズしました。")

# Botを起動
if DISCORD_TOKEN:
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"Bot起動中にエラーが発生しました: {e}")
        if db_pool:
            db_pool.closeall()
            logging.info("データベース接続プールをクローズしました。")
else:
    logging.error("DISCORD_TOKENが設定されていません。")
