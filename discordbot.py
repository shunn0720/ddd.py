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
import json

class DatabaseQueryError(Exception):
    pass

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

DATABASE_URL = os.getenv("DATABASE_URL")

try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1, maxconn=10, dsn=DATABASE_URL, sslmode='require'
    )
except psycopg2.Error as e:
    logging.error(f"データベース接続プールの初期化中にエラー: {e}")
    db_pool = None

def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise psycopg2.Error("データベース接続プールが初期化されていません。")
    except psycopg2.Error as e:
        logging.error(f"データベース接続中にエラー: {e}")
        return None

def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except psycopg2.Error as e:
        logging.error(f"データベース接続のリリース中にエラー: {e}")

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

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376
FAVORITE_REACTION_ID = 1304690627723657267
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495
SPECIAL_EXCLUDE_AUTHOR = 695096014482440244

AUTHORIZED_USER_ID = 822460191118721034  # 許可されたユーザーID

last_chosen_authors = {}
current_panel_message_id = None  # グローバル変数として現在のパネルメッセージIDを保持

async def get_reactions_dict(message):
    reactions = {}
    for reaction in message.reactions:
        if hasattr(reaction.emoji, 'id'):
            users = [user.id async for user in reaction.users()]
            reactions[str(reaction.emoji.id)] = users
    return reactions

async def save_message_to_db(message):
    conn = get_db_connection()
    if not conn:
        return
    try:
        reactions_dict = await get_reactions_dict(message)
        reactions_json = json.dumps(reactions_dict)
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET reactions = EXCLUDED.reactions
            """, (
                message.id,
                THREAD_ID,
                message.author.id,
                reactions_json,
                message.content
            ))
            conn.commit()
    except psycopg2.Error as e:
        logging.error(f"メッセージ保存中にエラー: {e}")
    finally:
        release_db_connection(conn)

async def update_reactions_in_db(message_id):
    channel = bot.get_channel(THREAD_ID)
    if channel is None:
        logging.error(f"チャンネル {THREAD_ID} が見つかりませんでした。")
        return
    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        logging.error(f"メッセージ {message_id} が見つかりませんでした。")
        return
    except discord.Forbidden:
        logging.error(f"メッセージ {message_id} へのアクセスが拒否されました。")
        return
    except discord.HTTPException as e:
        logging.error(f"メッセージ {message_id} の取得中にエラー: {e}")
        return

    await save_message_to_db(message)

def user_reacted(msg, reaction_id, user_id):
    reaction_data = msg['reactions']
    if reaction_data is None:
        reaction_data = {}
    if isinstance(reaction_data, str):
        reaction_data = json.loads(reaction_data)
    users = reaction_data.get(str(reaction_id), [])
    return user_id in users

def get_random_message(thread_id, filter_func=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()
            for msg in messages:
                if msg['reactions'] is None:
                    msg['reactions'] = {}
                elif isinstance(msg['reactions'], str):
                    msg['reactions'] = json.loads(msg['reactions']) or {}

            if filter_func:
                messages = [m for m in messages if filter_func(m)]
            if not messages:
                return None
            return random.choice(messages)
    except psycopg2.Error as e:
        logging.error(f"データベース操作中にエラー: {e}")
        return None
    finally:
        release_db_connection(conn)

async def send_panel(channel):
    global current_panel_message_id
    if current_panel_message_id:
        try:
            panel_message = await channel.fetch_message(current_panel_message_id)
            await panel_message.delete()
            logging.info(f"以前のパネルメッセージ {current_panel_message_id} を削除しました。")
        except discord.NotFound:
            logging.warning(f"以前のパネルメッセージ {current_panel_message_id} が見つかりません。")
        except discord.HTTPException as e:
            logging.error(f"パネルメッセージ {current_panel_message_id} の削除中にエラー: {e}")

    embed = create_panel_embed()
    view = CombinedView()
    try:
        sent_message = await channel.send(embed=embed, view=view)
        current_panel_message_id = sent_message.id
        logging.info(f"新しいパネルメッセージ {current_panel_message_id} を送信しました。")
    except discord.HTTPException as e:
        logging.error(f"パネルメッセージの送信中にエラー: {e}")

class CombinedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

def create_panel_embed():
    embed = discord.Embed(
        description=("ランダム投稿を選んでくれる！"),
        color=discord.Color.magenta()
    )
    return embed

@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    if interaction.user.id != AUTHORIZED_USER_ID:
        await interaction.response.send_message("このコマンドは使用できません。", ephemeral=True)
        return
    await interaction.response.defer()
    await send_panel(interaction.channel)

@bot.tree.command(name="update_db")
async def update_db(interaction: discord.Interaction):
    if interaction.user.id != AUTHORIZED_USER_ID:
        await interaction.response.send_message("このコマンドは使用できません。", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        await save_all_messages_to_db()
        await interaction.followup.send("全てのメッセージを保存しました。", ephemeral=True)
    except Exception as e:
        logging.error(f"update_dbコマンド中にエラーが発生しました: {e}")
        await interaction.followup.send(f"エラー: {e}", ephemeral=True)

@bot.event
async def on_ready():
    logging.info(f"Botが起動しました！ {bot.user}")
    try:
        synced = await bot.tree.sync()
        logging.info(f"スラッシュコマンドが同期されました。: {synced}")
    except Exception as e:
        logging.error(f"スラッシュコマンドの同期中にエラー: {e}")

if DISCORD_TOKEN:
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"Bot起動中にエラーが発生しました: {e}")
else:
    logging.error("DISCORD_TOKENが設定されていません。")
