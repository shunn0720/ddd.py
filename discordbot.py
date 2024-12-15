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

last_chosen_authors = {}
fixed_message_id = None  # Embed を固定するメッセージ ID を格納

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

class CombinedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def get_author_name(self, author_id):
        user = bot.get_user(author_id)
        if user is None:
            try:
                user = await bot.fetch_user(author_id)
            except discord.NotFound:
                user = None
        return user.display_name if user and user.display_name else (user.name if user else "不明なユーザー")

    async def handle_selection(self, interaction, random_message):
        try:
            if random_message:
                last_chosen_authors[interaction.user.id] = random_message['author_id']
                author_name = await self.get_author_name(random_message['author_id'])
                await interaction.channel.send(
                    f"{interaction.user.mention} さんには、{author_name} さんが投稿したこの本がおすすめだよ！\n"
                    f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
                )
            else:
                await interaction.channel.send(
                    f"{interaction.user.mention} 条件に合う投稿が見つかりませんでした。"
                )
        except Exception as e:
            logging.error(f"メッセージの取得または応答中にエラーが発生しました: {e}")
            await interaction.channel.send(
                f"{interaction.user.mention} 投稿を読み込む際にエラーが発生しました。しばらくしてから再試行してください。"
            )

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0)
    async def random_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            def filter_func(msg):
                if msg['author_id'] == interaction.user.id:
                    return False
                if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                    return False
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    return False
                return True

            random_message = get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            await interaction.channel.send(str(e))

def create_panel_embed():
    embed = discord.Embed(
        description=(
            "🎯エロ漫画ルーレット\n\n"
            "botがエロ漫画を選んでくれるよ！\n\n"
            "🔵：自分の除外しない\n"
            "🔴：自分の除外する\n\n"
            "【ランダム】全体から選ぶ\n"
        ),
        color=discord.Color.magenta()
    )
    return embed

@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    global fixed_message_id
    embed = create_panel_embed()
    view = CombinedView()
    if fixed_message_id:
        try:
            channel = interaction.channel
            if channel:
                message = await channel.fetch_message(fixed_message_id)
                await message.edit(embed=embed, view=view)
        except discord.NotFound:
            pass
    else:
        message = await interaction.channel.send(embed=embed, view=view)
        fixed_message_id = message.id
